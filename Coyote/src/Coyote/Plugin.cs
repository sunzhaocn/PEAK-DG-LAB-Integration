using BepInEx;
using BepInEx.Logging;
using UnityEngine;
using UnityEngine.SceneManagement;

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;

namespace Coyote;

[BepInAutoPlugin]
public partial class Plugin : BaseUnityPlugin
{
    internal static ManualLogSource Log { get; private set; } = null!;

    // ============================================================
    // 基础配置
    // ============================================================

    private const float 发送间隔 = 0.1f;      // 10 Hz
    private const float 端口检查间隔 = 1.0f;  // 热更新 Coyote.Network.json
    private const float 扩展快照间隔 = 0.5f;  // 反射快照不需要 10 Hz

    private const string 默认Python地址 = "127.0.0.1";
    private const int 默认Python端口 = 8765;

    private float timer = 0f;
    private float portTimer = 0f;
    private float snapshotTimer = 0f;

    private string python地址 = 默认Python地址;
    private int python端口 = 默认Python端口;

    private UdpClient? udp;
    private IPEndPoint? python终点;

    // ============================================================
    // 扩展遥测缓存
    // ============================================================

    private readonly Dictionary<Type, MemberInfo[]> 成员缓存 = new();

    private Dictionary<string, string> 最近CharacterData快照 = new();
    private Dictionary<string, string> 最近ItemSystem快照 = new();

    private int 上次手持实例ID = 0;
    private string 上次手持物名 = "";
    private Dictionary<string, string> 上次手持关键状态 = new();

    private float 上次饥饿 = 0f;

    // 跳跃事件序号。
    // 没有使用未经确认的“直接 jump 字段”，
    // 而是根据“接地 -> 离地 + 向上速度”推断一次跳跃事件。
    private bool 上次有角色 = false;
    private bool 上次接地 = true;
    private int 跳跃序号 = 0;

    private readonly 物品事件 最近物品事件 = new();
    private readonly 物品事件 最近使用物品 = new();
    private readonly 物品事件 最近食用物品 = new();

    private sealed class 物品事件
    {
        public string id = "";
        public string item = "";
        public string detail = "";
        public bool inferred = false;
    }

    // PEAK 当前真实关卡信息。scene 仅保留为 Unity 内部地图名，
    // 对用户展示的关卡优先使用 MapHandler / MountainProgressHandler。
    private sealed class 关卡信息
    {
        public int segmentIndex = -1;
        public int stageNumber = 0;
        public string stageName = "";
        public string biome = "";
        public string stageDisplay = "";
    }

    // ============================================================
    // 启动
    // ============================================================

    private void Awake()
    {
        Log = Logger;

        try
        {
            udp = new UdpClient();

            读取网络配置(force: true);
            更新Python终点();

            Log.LogInfo(
                $"Coyote 已启动，UDP -> {python地址}:{python端口}"
            );

            foreach (
                CharacterAfflictions.STATUSTYPE 类型
                in Enum.GetValues(
                    typeof(CharacterAfflictions.STATUSTYPE)
                )
            )
            {
                Log.LogInfo(
                    $"PEAK 状态 {(int)类型} = {类型}"
                );
            }
        }
        catch (Exception ex)
        {
            Log.LogError(
                $"Coyote 初始化失败: {ex}"
            );
        }
    }

    // ============================================================
    // Update
    // ============================================================

    private void Update()
    {
        timer += Time.deltaTime;
        portTimer += Time.deltaTime;
        snapshotTimer += Time.deltaTime;

        if (portTimer >= 端口检查间隔)
        {
            portTimer = 0f;
            读取网络配置(force: false);
        }

        if (timer < 发送间隔)
            return;

        timer = 0f;

        Character 玩家 = Character.localCharacter;

        string sceneName = "";

        try
        {
            sceneName =
                SceneManager.GetActiveScene().name
                ?? "";
        }
        catch
        {
            sceneName = "";
        }

        // --------------------------------------------------------
        // 大厅 / 主菜单 / 加载：没有本地 Character 时仍发 heartbeat。
        // Python 因此不再把“无角色遥测”误判成游戏关闭。
        // --------------------------------------------------------
        if (
            玩家 == null ||
            玩家.data == null ||
            玩家.refs == null
        )
        {
            上次有角色 = false;
            上次接地 = true;

            发送到Python(
                构造大厅心跳(sceneName)
            );

            return;
        }

        CharacterAfflictions 状态管理器 =
            玩家.refs.afflictions;

        if (
            状态管理器 == null ||
            状态管理器.currentStatuses == null
        )
        {
            上次有角色 = false;
            上次接地 = true;

            发送到Python(
                构造大厅心跳(sceneName)
            );

            return;
        }

        float[] 状态 =
            状态管理器.currentStatuses;

        float 受伤值 =
            状态.Length > 0
            ? Mathf.Clamp01(状态[0])
            : 0f;

        float 血量 =
            (1f - 受伤值) * 100f;

        float 当前体力 =
            玩家.data.currentStamina;

        float 体力上限 =
            玩家.GetMaxStamina();

        float 额外体力 =
            玩家.data.extraStamina;

        bool 死亡 =
            玩家.data.dead;

        bool 昏迷 =
            玩家.data.passedOut;

        bool 攀爬 =
            玩家.data.isClimbing;

        bool 接地 =
            玩家.data.isGrounded;

        bool 蹲下 =
            玩家.data.isCrouching;

        Vector3 世界坐标 =
            玩家.transform.position;

        Vector3 世界旋转 =
            玩家.transform.eulerAngles;

        Vector3 地面坐标 =
            玩家.data.groundPos;

        Vector3 看向方向 =
            玩家.data.lookDirection;

        Vector3 速度 =
            Vector3.zero;

        // 不直接编译期引用 Rigidbody.linearVelocity / velocity。
        // 不同 Unity 版本在这里存在 API 差异，反射读取更兼容。
        try
        {
            Rigidbody rb =
                玩家.GetComponent<Rigidbody>();

            if (rb != null)
            {
                object? 速度对象 =
                    获取成员值按名称(
                        rb,
                        "linearVelocity"
                    )
                    ??
                    获取成员值按名称(
                        rb,
                        "velocity"
                    );

                if (速度对象 is Vector3)
                    速度 = (Vector3)速度对象;
            }
        }
        catch
        {
            速度 = Vector3.zero;
        }

        float 速度大小 =
            速度.magnitude;

        if (
            上次有角色 &&
            上次接地 &&
            !接地 &&
            速度.y > 0.35f
        )
        {
            跳跃序号++;
        }

        上次有角色 = true;
        上次接地 = 接地;

        float sinceClimb =
            玩家.data.sinceClimb;

        string climbHandle =
            Unity对象名称(
                玩家.data.currentClimbHandle
            );

        float currentHeadHeight =
            玩家.data.currentHeadHeight;

        float targetHeadHeight =
            玩家.data.targetHeadHeight;

        float targetHipHeight =
            玩家.data.targetHipHeight;

        object? 当前物品 = null;

        try
        {
            当前物品 =
                玩家.data.currentItem;
        }
        catch
        {
            当前物品 = null;
        }

        // --------------------------------------------------------
        // 低频扩展快照：自动把 CharacterData / CharacterItems 中
        // 可安全序列化的简单值暴露给 Python。
        //
        // 这样 PEAK 更新新增公开/私有简单状态时，GUI 的“全部遥测”
        // 仍然能看到，不需要我们猜字段名。
        // --------------------------------------------------------
        if (snapshotTimer >= 扩展快照间隔)
        {
            snapshotTimer = 0f;

            最近CharacterData快照 =
                获取简单快照(
                    玩家.data,
                    140
                );

            object? itemSystem = null;

            try
            {
                itemSystem =
                    玩家.refs.items;
            }
            catch
            {
                itemSystem = null;
            }

            最近ItemSystem快照 =
                获取简单快照(
                    itemSystem,
                    100
                );
        }

        Dictionary<string, string> 当前物品关键状态 =
            获取关键物品状态(
                当前物品
            );

        更新物品事件(
            当前物品,
            当前物品关键状态,
            状态
        );

        List<string> 口袋物品 =
            获取口袋物品(
                玩家
            );

        List<string> 背包物品 =
            获取背包物品(
                玩家
            );

        关卡信息 当前关卡 =
            读取当前关卡();

        string json =
            构造局内JSON(
                sceneName,
                当前关卡,
                玩家,
                状态,
                血量,
                受伤值,
                当前体力,
                体力上限,
                额外体力,
                死亡,
                昏迷,
                攀爬,
                接地,
                蹲下,
                世界坐标,
                世界旋转,
                地面坐标,
                看向方向,
                速度,
                速度大小,
                sinceClimb,
                climbHandle,
                currentHeadHeight,
                targetHeadHeight,
                targetHipHeight,
                当前物品,
                当前物品关键状态,
                口袋物品,
                背包物品
            );

        发送到Python(
            json
        );
    }

    // ============================================================
    // 网络配置
    // ============================================================

    private string 网络配置路径 =>
        Path.Combine(
            Paths.ConfigPath,
            "Coyote.Network.json"
        );

    private DateTime 上次网络配置写入时间 =
        DateTime.MinValue;

    private void 读取网络配置(bool force)
    {
        try
        {
            string path =
                网络配置路径;

            if (!File.Exists(path))
            {
                if (force)
                {
                    Directory.CreateDirectory(
                        Paths.ConfigPath
                    );

                    File.WriteAllText(
                        path,
                        "{\n" +
                        "  \"pythonHost\": \"127.0.0.1\",\n" +
                        "  \"pythonPort\": 8765\n" +
                        "}",
                        Encoding.UTF8
                    );
                }

                return;
            }

            DateTime writeTime =
                File.GetLastWriteTimeUtc(
                    path
                );

            if (
                !force &&
                writeTime ==
                上次网络配置写入时间
            )
                return;

            上次网络配置写入时间 =
                writeTime;

            string text =
                File.ReadAllText(
                    path,
                    Encoding.UTF8
                );

            Match hostMatch =
                Regex.Match(
                    text,
                    "\"pythonHost\"\\s*:\\s*\"([^\"]+)\""
                );

            Match portMatch =
                Regex.Match(
                    text,
                    "\"pythonPort\"\\s*:\\s*(\\d+)"
                );

            string newHost =
                hostMatch.Success
                ? hostMatch.Groups[1].Value.Trim()
                : 默认Python地址;

            int newPort =
                默认Python端口;

            if (portMatch.Success)
            {
                int parsed;

                if (
                    int.TryParse(
                        portMatch.Groups[1].Value,
                        out parsed
                    )
                    &&
                    parsed >= 1024
                    &&
                    parsed <= 65535
                )
                {
                    newPort =
                        parsed;
                }
            }

            if (
                newHost != python地址 ||
                newPort != python端口
            )
            {
                python地址 =
                    newHost;

                python端口 =
                    newPort;

                更新Python终点();

                Log.LogInfo(
                    $"Coyote UDP 目标已更新 -> {python地址}:{python端口}"
                );
            }
        }
        catch (Exception ex)
        {
            Log.LogWarning(
                $"读取 Coyote.Network.json 失败: {ex.Message}"
            );
        }
    }

    private void 更新Python终点()
    {
        python终点 =
            new IPEndPoint(
                IPAddress.Parse(
                    python地址
                ),
                python端口
            );
    }

    // ============================================================
    // 物品事件
    // ============================================================

    private void 更新物品事件(
        object? 当前物品,
        Dictionary<string, string> 当前关键状态,
        float[] 当前状态
    )
    {
        int currentId =
            Unity实例ID(
                当前物品
            );

        string currentName =
            Unity对象名称(
                当前物品
            );

        float currentHunger =
            当前状态.Length > 1
            ? 当前状态[1]
            : 上次饥饿;

        // 手持变化
        if (
            currentId != 上次手持实例ID
        )
        {
            if (
                上次手持实例ID != 0 ||
                currentId != 0
            )
            {
                设置事件(
                    最近物品事件,
                    currentName.Length > 0
                        ? currentName
                        : 上次手持物名,
                    $"{(上次手持物名.Length > 0 ? 上次手持物名 : "空手")} -> {(currentName.Length > 0 ? currentName : "空手")}",
                    false
                );
            }

            // 食用/消耗推断：
            // 若上一帧持有物品，本帧物品离手/变更，同时 Hunger 明显下降，
            // 很可能刚完成了一次食用。明确打 inferred=true。
            if (
                上次手持物名.Length > 0 &&
                currentHunger <
                上次饥饿 - 0.001f
            )
            {
                设置事件(
                    最近食用物品,
                    上次手持物名,
                    $"Hunger {上次饥饿:F3} -> {currentHunger:F3}",
                    true
                );
            }
        }

        // 同一个物品仍在手里：检测 uses/charge/fuel/durability 等关键值下降。
        if (
            currentId != 0 &&
            currentId == 上次手持实例ID
        )
        {
            foreach (
                KeyValuePair<string, string> kv
                in 当前关键状态
            )
            {
                string oldValue;

                if (
                    !上次手持关键状态.TryGetValue(
                        kv.Key,
                        out oldValue
                    )
                )
                    continue;

                double oldNumber;
                double newNumber;

                if (
                    double.TryParse(
                        oldValue,
                        NumberStyles.Any,
                        CultureInfo.InvariantCulture,
                        out oldNumber
                    )
                    &&
                    double.TryParse(
                        kv.Value,
                        NumberStyles.Any,
                        CultureInfo.InvariantCulture,
                        out newNumber
                    )
                    &&
                    newNumber < oldNumber
                )
                {
                    string lower =
                        kv.Key.ToLowerInvariant();

                    if (
                        lower.Contains("use") ||
                        lower.Contains("charge") ||
                        lower.Contains("fuel") ||
                        lower.Contains("durab") ||
                        lower.Contains("amount") ||
                        lower.Contains("count")
                    )
                    {
                        设置事件(
                            最近使用物品,
                            currentName,
                            $"{kv.Key}: {oldValue} -> {kv.Value}",
                            false
                        );

                        break;
                    }
                }
            }
        }

        上次手持实例ID =
            currentId;

        上次手持物名 =
            currentName;

        上次手持关键状态 =
            new Dictionary<string, string>(
                当前关键状态
            );

        上次饥饿 =
            currentHunger;
    }

    private static void 设置事件(
        物品事件 事件,
        string item,
        string detail,
        bool inferred
    )
    {
        事件.id =
            DateTime.UtcNow.Ticks.ToString(
                CultureInfo.InvariantCulture
            );

        事件.item =
            item ?? "";

        事件.detail =
            detail ?? "";

        事件.inferred =
            inferred;
    }

    // ============================================================
    // 反射快照
    // ============================================================

    private MemberInfo[] 获取成员(Type type)
    {
        MemberInfo[] cached;

        if (
            成员缓存.TryGetValue(
                type,
                out cached!
            )
        )
            return cached;

        List<MemberInfo> result =
            new List<MemberInfo>();

        BindingFlags flags =
            BindingFlags.Instance |
            BindingFlags.Public |
            BindingFlags.NonPublic;

        foreach (
            FieldInfo field
            in type.GetFields(flags)
        )
        {
            result.Add(
                field
            );
        }

        foreach (
            PropertyInfo prop
            in type.GetProperties(flags)
        )
        {
            if (
                prop.GetIndexParameters().Length == 0 &&
                prop.GetMethod != null
            )
            {
                result.Add(
                    prop
                );
            }
        }

        cached =
            result.ToArray();

        成员缓存[type] =
            cached;

        return cached;
    }

    private Dictionary<string, string> 获取简单快照(
        object? target,
        int maxMembers
    )
    {
        Dictionary<string, string> result =
            new Dictionary<string, string>();

        if (target == null)
            return result;

        try
        {
            foreach (
                MemberInfo member
                in 获取成员(
                    target.GetType()
                )
            )
            {
                if (
                    result.Count >=
                    maxMembers
                )
                    break;

                object? value =
                    获取成员值(
                        target,
                        member
                    );

                string? simple =
                    简单值转字符串(
                        value
                    );

                if (simple == null)
                    continue;

                result[
                    member.Name
                ] = simple;
            }
        }
        catch
        {
            // 单个运行时成员失败不应影响整个遥测。
        }

        return result;
    }

    private Dictionary<string, string> 获取关键物品状态(
        object? item
    )
    {
        Dictionary<string, string> result =
            new Dictionary<string, string>();

        if (item == null)
            return result;

        string[] tokens = new string[]
        {
            "use",
            "charge",
            "fuel",
            "durab",
            "state",
            "weight",
            "amount",
            "count",
            "value",
            "slot",
            "id"
        };

        try
        {
            foreach (
                MemberInfo member
                in 获取成员(
                    item.GetType()
                )
            )
            {
                string lower =
                    member.Name.ToLowerInvariant();

                bool wanted =
                    false;

                foreach (
                    string token
                    in tokens
                )
                {
                    if (
                        lower.Contains(
                            token
                        )
                    )
                    {
                        wanted = true;
                        break;
                    }
                }

                if (!wanted)
                    continue;

                object? value =
                    获取成员值(
                        item,
                        member
                    );

                string? simple =
                    简单值转字符串(
                        value
                    );

                if (simple != null)
                    result[
                        member.Name
                    ] = simple;

                if (
                    result.Count >= 50
                )
                    break;
            }
        }
        catch
        {
        }

        return result;
    }

    private static object? 获取成员值(
        object target,
        MemberInfo member
    )
    {
        try
        {
            FieldInfo? field =
                member as FieldInfo;

            if (field != null)
                return field.GetValue(
                    target
                );

            PropertyInfo? prop =
                member as PropertyInfo;

            if (prop != null)
                return prop.GetValue(
                    target,
                    null
                );
        }
        catch
        {
        }

        return null;
    }

    private static string? 简单值转字符串(
        object? value
    )
    {
        if (value == null)
            return null;

        Type type =
            value.GetType();

        if (
            type.IsPrimitive ||
            type.IsEnum ||
            value is decimal
        )
        {
            return Convert.ToString(
                value,
                CultureInfo.InvariantCulture
            );
        }

        if (value is string)
            return (string)value;

        if (value is Vector2)
        {
            Vector2 v =
                (Vector2)value;

            return string.Format(
                CultureInfo.InvariantCulture,
                "{0:F4},{1:F4}",
                v.x,
                v.y
            );
        }

        if (value is Vector3)
        {
            Vector3 v =
                (Vector3)value;

            return string.Format(
                CultureInfo.InvariantCulture,
                "{0:F4},{1:F4},{2:F4}",
                v.x,
                v.y,
                v.z
            );
        }

        if (value is Quaternion)
        {
            Quaternion q =
                (Quaternion)value;

            return string.Format(
                CultureInfo.InvariantCulture,
                "{0:F4},{1:F4},{2:F4},{3:F4}",
                q.x,
                q.y,
                q.z,
                q.w
            );
        }

        UnityEngine.Object? unityObj =
            value as UnityEngine.Object;

        if (unityObj != null)
            return unityObj.name;

        return null;
    }

    // ============================================================
    // 口袋 / 背包物品
    // ============================================================

    private List<string> 获取口袋物品(
        Character 玩家
    )
    {
        List<string> result =
            new List<string>();

        try
        {
            if (
                玩家.player == null ||
                玩家.player.itemSlots == null
            )
                return result;

            foreach (
                ItemSlot slot
                in 玩家.player.itemSlots
            )
            {
                if (slot.IsEmpty())
                    continue;

                string name =
                    Unity对象名称(
                        slot.prefab
                    );

                if (!string.IsNullOrEmpty(name))
                    result.Add(name);
            }
        }
        catch
        {
        }

        return result;
    }

    private List<string> 获取背包物品(
        Character 玩家
    )
    {
        List<string> result =
            new List<string>();

        try
        {
            if (
                玩家 == null ||
                玩家.player == null ||
                玩家.player.backpackSlot == null
            )
            {
                return result;
            }

            BackpackSlot backpackSlot =
                玩家.player.backpackSlot;

            // 当前 PEAK 版本直接按 ItemSlot 检查背包槽是否为空。
            ItemSlot itemSlot =
                (ItemSlot)backpackSlot;

            if (
                itemSlot == null ||
                itemSlot.IsEmpty() ||
                itemSlot.data == null
            )
            {
                return result;
            }

            // 当前 PEAK API 的 TryGetDataEntry 第二个参数要求 out。
            BackpackData backpackData;

            bool found =
                itemSlot.data
                .TryGetDataEntry<BackpackData>(
                    (DataEntryKey)7,
                    out backpackData
                );

            if (
                !found ||
                backpackData == null ||
                backpackData.itemSlots == null
            )
            {
                return result;
            }

            foreach (
                ItemSlot slot
                in backpackData.itemSlots
            )
            {
                if (
                    slot == null ||
                    slot.IsEmpty() ||
                    slot.prefab == null
                )
                {
                    continue;
                }

                string name =
                    Unity对象名称(
                        slot.prefab
                    );

                if (
                    !string.IsNullOrEmpty(
                        name
                    )
                )
                {
                    result.Add(
                        name
                    );
                }
            }
        }
        catch (
            Exception ex
        )
        {
            Logger.LogWarning(
                "读取背包物品失败："
                + ex.Message
            );
        }

        return result;
    }

    private static void 追加字符串数组(
        StringBuilder json,
        IList<string> values
    )
    {
        json.Append("[");

        if (values != null)
        {
            for (
                int i = 0;
                i < values.Count;
                i++
            )
            {
                if (i > 0)
                {
                    json.Append(",");
                }

                json.Append("\"");

                json.Append(
                    JSON字符串转义(
                        values[i]
                        ?? ""
                    )
                );

                json.Append("\"");
            }
        }

        json.Append("]");
    }



    // ============================================================
    // PEAK 真实关卡 / Segment
    // ============================================================

    private 关卡信息 读取当前关卡()
    {
        关卡信息 info =
            new 关卡信息();

        try
        {
            MapHandler mapHandler =
                UnityEngine.Object
                .FindFirstObjectByType<MapHandler>();

            if (mapHandler == null)
                return info;

            Segment segment =
                mapHandler.GetCurrentSegment();

            int index =
                (int)segment;

            info.segmentIndex =
                index;

            if (index >= 0)
            {
                info.stageNumber =
                    index + 1;
            }

            try
            {
                object biome =
                    mapHandler.GetCurrentBiome();

                info.biome =
                    biome != null
                    ? biome.ToString() ?? ""
                    : "";
            }
            catch
            {
                info.biome = "";
            }

            try
            {
                MountainProgressHandler progress =
                    UnityEngine.Object
                    .FindFirstObjectByType<MountainProgressHandler>();

                if (
                    progress != null &&
                    progress.progressPoints != null &&
                    index >= 0 &&
                    index < progress.progressPoints.Length
                )
                {
                    string title =
                        progress.progressPoints[index].title
                        ?? "";

                    if (!string.IsNullOrWhiteSpace(title))
                    {
                        info.stageName =
                            title.Trim();
                    }
                }
            }
            catch
            {
                info.stageName = "";
            }

            if (string.IsNullOrWhiteSpace(info.stageName))
            {
                info.stageName =
                    关卡默认名称(index);
            }

            if (info.stageNumber > 0)
            {
                info.stageDisplay =
                    $"第{info.stageNumber}关";

                if (!string.IsNullOrWhiteSpace(info.stageName))
                {
                    info.stageDisplay +=
                        $" · {info.stageName}";
                }
            }
        }
        catch
        {
            // 地图切换 / 加载期间读取不到 MapHandler 时保持空值，
            // 不影响其余 PEAK 遥测和电击规则。
        }

        return info;
    }

    private static string 关卡默认名称(
        int index
    )
    {
        switch (index)
        {
            case 0: return "Shore";
            case 1: return "Tropics";
            case 2: return "Alpine";
            case 3: return "Caldera";
            case 4: return "The Kiln";
            case 5: return "PEAK";
            default: return "";
        }
    }

    // ============================================================
    // JSON
    // ============================================================

    private string 构造大厅心跳(
        string sceneName
    )
    {
        StringBuilder json =
            new StringBuilder();

        json.Append("{");

        追加数字(
            json,
            "telemetryVersion",
            5,
            first: true
        );

        追加字符串(
            json,
            "scene",
            sceneName
        );

        追加布尔(
            json,
            "hasCharacter",
            false
        );

        追加字符串(
            json,
            "phase",
            "lobby_or_loading"
        );

        json.Append("}");

        return json.ToString();
    }

    private string 构造局内JSON(
        string sceneName,
        关卡信息 当前关卡,
        Character 玩家,
        float[] 状态,
        float 血量,
        float 受伤值,
        float 当前体力,
        float 体力上限,
        float 额外体力,
        bool 死亡,
        bool 昏迷,
        bool 攀爬,
        bool 接地,
        bool 蹲下,
        Vector3 世界坐标,
        Vector3 世界旋转,
        Vector3 地面坐标,
        Vector3 看向方向,
        Vector3 速度,
        float 速度大小,
        float sinceClimb,
        string climbHandle,
        float currentHeadHeight,
        float targetHeadHeight,
        float targetHipHeight,
        object? 当前物品,
        Dictionary<string, string> 当前物品关键状态,
        List<string> 口袋物品,
        List<string> 背包物品
    )
    {
        StringBuilder json =
            new StringBuilder();

        json.Append("{");

        追加数字(
            json,
            "telemetryVersion",
            5,
            first: true
        );

        追加字符串(
            json,
            "scene",
            sceneName
        );

        追加数字(
            json,
            "segmentIndex",
            当前关卡.segmentIndex
        );

        追加数字(
            json,
            "stageNumber",
            当前关卡.stageNumber
        );

        追加字符串(
            json,
            "stageName",
            当前关卡.stageName
        );

        追加字符串(
            json,
            "biome",
            当前关卡.biome
        );

        追加字符串(
            json,
            "stageDisplay",
            当前关卡.stageDisplay
        );

        追加布尔(
            json,
            "hasCharacter",
            true
        );

        追加字符串(
            json,
            "phase",
            "in_game"
        );

        追加数字(
            json,
            "jumpSeq",
            跳跃序号
        );

        追加浮点(
            json,
            "hp",
            血量
        );

        追加浮点(
            json,
            "injury",
            受伤值
        );

        追加浮点(
            json,
            "staminaCurrent",
            当前体力
        );

        追加浮点(
            json,
            "staminaMax",
            体力上限
        );

        追加浮点(
            json,
            "staminaExtra",
            额外体力
        );

        追加布尔(
            json,
            "dead",
            死亡
        );

        追加布尔(
            json,
            "passedOut",
            昏迷
        );

        追加布尔(
            json,
            "climbing",
            攀爬
        );

        追加布尔(
            json,
            "grounded",
            接地
        );

        追加布尔(
            json,
            "crouching",
            蹲下
        );

        追加Vector3(
            json,
            "position",
            世界坐标
        );

        追加Vector3(
            json,
            "rotation",
            世界旋转
        );

        追加Vector3(
            json,
            "groundPos",
            地面坐标
        );

        追加Vector3(
            json,
            "lookDirection",
            看向方向
        );

        追加Vector3(
            json,
            "velocity",
            速度
        );

        追加浮点(
            json,
            "speed",
            速度大小
        );

        追加浮点(
            json,
            "sinceClimb",
            sinceClimb
        );

        追加字符串(
            json,
            "currentClimbHandle",
            climbHandle
        );

        追加浮点(
            json,
            "currentHeadHeight",
            currentHeadHeight
        );

        追加浮点(
            json,
            "targetHeadHeight",
            targetHeadHeight
        );

        追加浮点(
            json,
            "targetHipHeight",
            targetHipHeight
        );

        // 当前物品
        json.Append(",\"heldItem\":{");

        追加字符串(
            json,
            "name",
            Unity对象名称(
                当前物品
            ),
            first: true
        );

        追加字符串(
            json,
            "type",
            当前物品 != null
                ? 当前物品.GetType().Name
                : ""
        );

        追加数字(
            json,
            "instanceId",
            Unity实例ID(
                当前物品
            )
        );

        json.Append(",\"details\":");
        追加字符串字典(
            json,
            当前物品关键状态
        );

        json.Append("}");

        // CharacterItems 当前选择槽位等简单状态
        string selectedSlot =
            "";

        try
        {
            object? items =
                玩家.refs.items;

            object? selected =
                items != null
                ? 获取成员值按名称(
                    items,
                    "currentSelectedSlot"
                )
                : null;

            selectedSlot =
                Convert.ToString(
                    selected,
                    CultureInfo.InvariantCulture
                )
                ?? "";
        }
        catch
        {
            selectedSlot = "";
        }

        json.Append(",\"inventory\":{");

        追加字符串(
            json,
            "selectedSlot",
            selectedSlot,
            first: true
        );

        json.Append(",\"pocketItems\":");
        追加字符串数组(
            json,
            口袋物品
        );

        json.Append(",\"backpackItems\":");
        追加字符串数组(
            json,
            背包物品
        );

        json.Append(",\"extra\":");
        追加字符串字典(
            json,
            最近ItemSystem快照
        );

        json.Append("}");

        // 所有 affliction
        json.Append(
            ",\"statuses\":["
        );

        for (
            int i = 0;
            i < 状态.Length;
            i++
        )
        {
            if (i > 0)
                json.Append(",");

            json.Append(
                状态[i].ToString(
                    "F4",
                    CultureInfo.InvariantCulture
                )
            );
        }

        json.Append("]");

        json.Append(
            ",\"statusNames\":["
        );

        for (
            int i = 0;
            i < 状态.Length;
            i++
        )
        {
            if (i > 0)
                json.Append(",");

            CharacterAfflictions.STATUSTYPE 类型 =
                (CharacterAfflictions.STATUSTYPE)i;

            json.Append("\"");
            json.Append(
                JSON字符串转义(
                    类型.ToString()
                )
            );
            json.Append("\"");
        }

        json.Append("]");

        json.Append(
            ",\"characterDataExtra\":"
        );

        追加字符串字典(
            json,
            最近CharacterData快照
        );

        json.Append(
            ",\"lastItemEvent\":"
        );

        追加事件(
            json,
            最近物品事件
        );

        json.Append(
            ",\"lastUsedItem\":"
        );

        追加事件(
            json,
            最近使用物品
        );

        json.Append(
            ",\"lastConsumedItem\":"
        );

        追加事件(
            json,
            最近食用物品
        );

        json.Append("}");

        return json.ToString();
    }

    private static object? 获取成员值按名称(
        object target,
        string name
    )
    {
        if (target == null)
            return null;

        try
        {
            Type type =
                target.GetType();

            BindingFlags flags =
                BindingFlags.Instance |
                BindingFlags.Public |
                BindingFlags.NonPublic;

            FieldInfo? field =
                type.GetField(
                    name,
                    flags
                );

            if (field != null)
                return field.GetValue(
                    target
                );

            PropertyInfo? prop =
                type.GetProperty(
                    name,
                    flags
                );

            if (
                prop != null &&
                prop.GetIndexParameters().Length == 0
            )
                return prop.GetValue(
                    target,
                    null
                );
        }
        catch
        {
        }

        return null;
    }

    private static void 追加事件(
        StringBuilder json,
        物品事件 事件
    )
    {
        json.Append("{");

        追加字符串(
            json,
            "id",
            事件.id,
            first: true
        );

        追加字符串(
            json,
            "item",
            事件.item
        );

        追加字符串(
            json,
            "detail",
            事件.detail
        );

        追加布尔(
            json,
            "inferred",
            事件.inferred
        );

        json.Append("}");
    }

    private static void 追加字符串字典(
        StringBuilder json,
        Dictionary<string, string> values
    )
    {
        json.Append("{");

        bool first =
            true;

        foreach (
            KeyValuePair<string, string> kv
            in values
        )
        {
            if (!first)
                json.Append(",");

            first =
                false;

            json.Append("\"");
            json.Append(
                JSON字符串转义(
                    kv.Key
                )
            );
            json.Append("\":\"");
            json.Append(
                JSON字符串转义(
                    kv.Value
                )
            );
            json.Append("\"");
        }

        json.Append("}");
    }

    private static void 追加Vector3(
        StringBuilder json,
        string key,
        Vector3 value
    )
    {
        json.Append(",\"");
        json.Append(
            JSON字符串转义(
                key
            )
        );
        json.Append("\":{");

        追加浮点(
            json,
            "x",
            value.x,
            first: true
        );

        追加浮点(
            json,
            "y",
            value.y
        );

        追加浮点(
            json,
            "z",
            value.z
        );

        json.Append("}");
    }

    private static void 追加字符串(
        StringBuilder json,
        string key,
        string value,
        bool first = false
    )
    {
        if (!first)
            json.Append(",");

        json.Append("\"");
        json.Append(
            JSON字符串转义(
                key
            )
        );
        json.Append("\":\"");
        json.Append(
            JSON字符串转义(
                value ?? ""
            )
        );
        json.Append("\"");
    }

    private static void 追加布尔(
        StringBuilder json,
        string key,
        bool value,
        bool first = false
    )
    {
        if (!first)
            json.Append(",");

        json.Append("\"");
        json.Append(
            JSON字符串转义(
                key
            )
        );
        json.Append("\":");
        json.Append(
            value
            ? "true"
            : "false"
        );
    }

    private static void 追加数字(
        StringBuilder json,
        string key,
        int value,
        bool first = false
    )
    {
        if (!first)
            json.Append(",");

        json.Append("\"");
        json.Append(
            JSON字符串转义(
                key
            )
        );
        json.Append("\":");
        json.Append(
            value.ToString(
                CultureInfo.InvariantCulture
            )
        );
    }

    private static void 追加浮点(
        StringBuilder json,
        string key,
        float value,
        bool first = false
    )
    {
        if (!first)
            json.Append(",");

        json.Append("\"");
        json.Append(
            JSON字符串转义(
                key
            )
        );
        json.Append("\":");
        json.Append(
            value.ToString(
                "F4",
                CultureInfo.InvariantCulture
            )
        );
    }

    private static string Unity对象名称(
        object? obj
    )
    {
        if (obj == null)
            return "";

        try
        {
            UnityEngine.Object? unityObj =
                obj as UnityEngine.Object;

            if (unityObj != null)
                return unityObj.name ?? "";

            return obj.ToString() ?? "";
        }
        catch
        {
            return "";
        }
    }

    private static int Unity实例ID(
        object? obj
    )
    {
        if (obj == null)
            return 0;

        try
        {
            UnityEngine.Object? unityObj =
                obj as UnityEngine.Object;

            if (unityObj != null)
                return unityObj.GetInstanceID();
        }
        catch
        {
        }

        return 0;
    }

    private static string JSON字符串转义(
        string 文本
    )
    {
        if (文本 == null)
            return "";

        return 文本
            .Replace("\\", "\\\\")
            .Replace("\"", "\\\"")
            .Replace("\r", "\\r")
            .Replace("\n", "\\n")
            .Replace("\t", "\\t");
    }

    // ============================================================
    // UDP
    // ============================================================

    private void 发送到Python(
        string 数据
    )
    {
        if (
            udp == null ||
            python终点 == null
        )
            return;

        try
        {
            byte[] bytes =
                Encoding.UTF8.GetBytes(
                    数据
                );

            udp.Send(
                bytes,
                bytes.Length,
                python终点
            );
        }
        catch (Exception ex)
        {
            Log.LogError(
                $"UDP 发送失败: {ex.Message}"
            );
        }
    }

    // ============================================================
    // 卸载
    // ============================================================

    private void OnDestroy()
    {
        try
        {
            udp?.Close();
            udp?.Dispose();
        }
        catch
        {
        }

        udp = null;
    }
}
