using UnityEngine;
using UnityEngine.SceneManagement;

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace Coyote;

// Multiplayer telemetry is split into a separate partial file so the existing
// local-player Plugin.cs remains untouched. Unity calls LateUpdate on the same
// compiled MonoBehaviour class.
public partial class Plugin
{
    private const float 多人发送间隔 = 0.20f;   // 5 Hz roster/status refresh
    private const float 多人扫描间隔 = 0.50f;   // expensive scene discovery only 2 Hz
    private const int 多人最大玩家数 = 64;

    private float 多人发送计时 = 0f;
    private float 多人扫描计时 = 0f;
    private readonly List<Character> 多人角色缓存 = new();

    private void LateUpdate()
    {
        多人发送计时 += Time.deltaTime;
        多人扫描计时 += Time.deltaTime;

        if (多人扫描计时 >= 多人扫描间隔)
        {
            多人扫描计时 = 0f;
            多人_刷新角色缓存();
        }

        if (多人发送计时 < 多人发送间隔)
            return;

        多人发送计时 = 0f;

        try
        {
            发送到Python(
                多人_构造JSON()
            );
        }
        catch (Exception ex)
        {
            // Do not spam the game log at 5 Hz if PEAK changes an internal field.
            if (Time.frameCount % 300 == 0)
            {
                Log.LogWarning(
                    $"多人遥测生成失败: {ex.Message}"
                );
            }
        }
    }

    private void 多人_刷新角色缓存()
    {
        多人角色缓存.RemoveAll(
            玩家 => 玩家 == null
        );

        try
        {
#pragma warning disable CS0618
            Character[] 全部角色 =
                UnityEngine.Object.FindObjectsOfType<Character>();
#pragma warning restore CS0618

            if (全部角色 == null)
                return;

            HashSet<int> 已存在 = new();

            foreach (Character 玩家 in 多人角色缓存)
            {
                if (玩家 != null)
                    已存在.Add(玩家.GetInstanceID());
            }

            foreach (Character 玩家 in 全部角色)
            {
                if (玩家 == null)
                    continue;

                int id = 玩家.GetInstanceID();

                if (已存在.Add(id))
                    多人角色缓存.Add(玩家);

                if (多人角色缓存.Count >= 多人最大玩家数)
                    break;
            }
        }
        catch
        {
            // Keep the previous cache if scene discovery temporarily fails.
        }
    }

    private string 多人_构造JSON()
    {
        Character 本地玩家 = Character.localCharacter;

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

        // Remove destroyed/disconnected Character objects immediately from the
        // outgoing roster, even between the slower discovery scans.
        多人角色缓存.RemoveAll(
            玩家 =>
                玩家 == null ||
                玩家.data == null ||
                玩家.refs == null ||
                玩家.refs.afflictions == null
        );

        StringBuilder sb = new StringBuilder(8192);

        sb.Append('{');
        sb.Append("\"_coyotePacketType\":\"multiplayer\",");
        sb.Append("\"version\":1,");
        sb.Append("\"scene\":");
        多人_JSON字符串(sb, sceneName);
        sb.Append(',');
        sb.Append("\"players\":[");

        int written = 0;

        foreach (Character 玩家 in 多人角色缓存)
        {
            if (玩家 == null)
                continue;

            try
            {
                if (!多人_写玩家(sb, 玩家, 本地玩家, sceneName, written > 0))
                    continue;

                written++;

                if (written >= 多人最大玩家数)
                    break;
            }
            catch
            {
                // One broken remote Character must not hide the rest of the roster.
            }
        }

        // If discovery has not run yet but the local Character exists, make sure
        // the local player still appears immediately.
        if (
            written == 0 &&
            本地玩家 != null &&
            本地玩家.data != null &&
            本地玩家.refs != null &&
            本地玩家.refs.afflictions != null
        )
        {
            try
            {
                if (多人_写玩家(sb, 本地玩家, 本地玩家, sceneName, false))
                    written++;
            }
            catch
            {
            }
        }

        sb.Append("],");
        sb.Append("\"playerCount\":");
        sb.Append(written.ToString(CultureInfo.InvariantCulture));
        sb.Append('}');

        return sb.ToString();
    }

    private bool 多人_写玩家(
        StringBuilder sb,
        Character 玩家,
        Character 本地玩家,
        string sceneName,
        bool prependComma
    )
    {
        if (
            玩家 == null ||
            玩家.data == null ||
            玩家.refs == null
        )
            return false;

        CharacterAfflictions 状态管理器 =
            玩家.refs.afflictions;

        if (
            状态管理器 == null ||
            状态管理器.currentStatuses == null
        )
            return false;

        float[] 状态 =
            状态管理器.currentStatuses;

        int injuryIndex =
            (int)CharacterAfflictions.STATUSTYPE.Injury;

        float injury =
            injuryIndex >= 0 && injuryIndex < 状态.Length
            ? Mathf.Clamp01(状态[injuryIndex])
            : 0f;

        float hp =
            Mathf.Clamp(
                (1f - injury) * 100f,
                0f,
                100f
            );

        float staminaCurrent =
            玩家.data.currentStamina;

        float staminaMax = 0f;

        try
        {
            staminaMax =
                玩家.GetMaxStamina();
        }
        catch
        {
            staminaMax = 0f;
        }

        float extraStamina =
            玩家.data.extraStamina;

        bool isLocal =
            玩家 == 本地玩家;

        bool fullyPassedOut =
            多人_读取Bool(
                玩家.data,
                "fullyPassedOut",
                false
            );

        Vector3 position =
            玩家.transform.position;

        float distance = 0f;

        if (
            本地玩家 != null &&
            本地玩家.transform != null
        )
        {
            distance =
                Vector3.Distance(
                    position,
                    本地玩家.transform.position
                );
        }

        string networkId =
            多人_读取网络标识(玩家);

        int instanceId =
            玩家.GetInstanceID();

        string playerId =
            networkId.Length > 0
            ? "net:" + networkId
            : "instance:" + instanceId.ToString(
                CultureInfo.InvariantCulture
            );

        string displayName =
            多人_读取玩家名称(
                玩家,
                isLocal
            );

        if (prependComma)
            sb.Append(',');

        sb.Append('{');

        多人_JSON字段(sb, "playerId", playerId, true);
        多人_JSON字段(sb, "networkId", networkId, true);
        多人_JSON字段(
            sb,
            "instanceId",
            instanceId.ToString(CultureInfo.InvariantCulture),
            true
        );
        多人_JSON字段(sb, "name", displayName, true);

        sb.Append("\"isLocal\":");
        sb.Append(isLocal ? "true" : "false");
        sb.Append(',');

        多人_JSON字段(sb, "scene", sceneName, true);

        sb.Append("\"hp\":");
        多人_JSON数字(sb, hp);
        sb.Append(',');
        sb.Append("\"hpMax\":100,");

        sb.Append("\"staminaCurrent\":");
        多人_JSON数字(sb, staminaCurrent);
        sb.Append(',');
        sb.Append("\"staminaMax\":");
        多人_JSON数字(sb, staminaMax);
        sb.Append(',');
        sb.Append("\"extraStamina\":");
        多人_JSON数字(sb, extraStamina);
        sb.Append(',');

        sb.Append("\"dead\":");
        sb.Append(玩家.data.dead ? "true" : "false");
        sb.Append(',');
        sb.Append("\"passedOut\":");
        sb.Append(玩家.data.passedOut ? "true" : "false");
        sb.Append(',');
        sb.Append("\"fullyPassedOut\":");
        sb.Append(fullyPassedOut ? "true" : "false");
        sb.Append(',');
        sb.Append("\"climbing\":");
        sb.Append(玩家.data.isClimbing ? "true" : "false");
        sb.Append(',');
        sb.Append("\"grounded\":");
        sb.Append(玩家.data.isGrounded ? "true" : "false");
        sb.Append(',');
        sb.Append("\"crouching\":");
        sb.Append(玩家.data.isCrouching ? "true" : "false");
        sb.Append(',');

        sb.Append("\"position\":{");
        sb.Append("\"x\":");
        多人_JSON数字(sb, position.x);
        sb.Append(',');
        sb.Append("\"y\":");
        多人_JSON数字(sb, position.y);
        sb.Append(',');
        sb.Append("\"z\":");
        多人_JSON数字(sb, position.z);
        sb.Append("},");

        sb.Append("\"distanceToLocal\":");
        多人_JSON数字(sb, distance);
        sb.Append(',');

        // Names and values are emitted side by side and the Python layer matches
        // by name. No multiplayer UI code relies on a hard-coded status index.
        sb.Append("\"statusNames\":[");

        for (int i = 0; i < 状态.Length; i++)
        {
            if (i > 0)
                sb.Append(',');

            string statusName =
                Enum.GetName(
                    typeof(CharacterAfflictions.STATUSTYPE),
                    i
                )
                ?? ("Status" + i.ToString(CultureInfo.InvariantCulture));

            多人_JSON字符串(
                sb,
                statusName
            );
        }

        sb.Append("],");
        sb.Append("\"statuses\":[");

        for (int i = 0; i < 状态.Length; i++)
        {
            if (i > 0)
                sb.Append(',');

            多人_JSON数字(
                sb,
                Mathf.Clamp01(状态[i])
            );
        }

        sb.Append(']');
        sb.Append('}');

        return true;
    }

    private string 多人_读取玩家名称(
        Character 玩家,
        bool isLocal
    )
    {
        string[] names = new string[]
        {
            "playerName",
            "PlayerName",
            "displayName",
            "DisplayName",
            "nickname",
            "Nickname",
            "nickName",
            "NickName",
            "username",
            "Username"
        };

        object?[] targets = new object?[]
        {
            玩家,
            玩家.data,
            玩家.refs
        };

        foreach (object? target in targets)
        {
            if (target == null)
                continue;

            foreach (string name in names)
            {
                object? value =
                    获取成员值按名称(
                        target,
                        name
                    );

                string text =
                    多人_简单字符串(value);

                if (
                    text.Length > 0 &&
                    !text.Contains("Character(", StringComparison.OrdinalIgnoreCase)
                )
                    return text;
            }
        }

        // Photon/network view owners commonly expose NickName even when the
        // Character itself does not. Use reflection to avoid a hard dependency.
        object? view =
            获取成员值按名称(玩家, "photonView")
            ?? 获取成员值按名称(玩家, "PhotonView")
            ?? 获取成员值按名称(玩家, "view")
            ?? 获取成员值按名称(玩家, "View");

        if (view != null)
        {
            object? owner =
                获取成员值按名称(view, "Owner")
                ?? 获取成员值按名称(view, "owner");

            if (owner != null)
            {
                foreach (string name in names)
                {
                    string text =
                        多人_简单字符串(
                            获取成员值按名称(
                                owner,
                                name
                            )
                        );

                    if (text.Length > 0)
                        return text;
                }
            }
        }

        if (isLocal)
            return "本地玩家";

        string unityName =
            玩家.gameObject != null
            ? (玩家.gameObject.name ?? "")
            : "";

        if (
            unityName.Length > 0 &&
            !unityName.Equals("Character(Clone)", StringComparison.OrdinalIgnoreCase) &&
            !unityName.Equals("Character", StringComparison.OrdinalIgnoreCase)
        )
            return unityName;

        return "玩家 " +
            Math.Abs(玩家.GetInstanceID())
            .ToString(CultureInfo.InvariantCulture);
    }

    // COYOTE_MULTIPLAYER_HARDENING_V6
    private string 多人_读取网络标识(
        Character 玩家
    )
    {
        string[] stableDirectNames = new string[]
        {
            "steamId", "SteamId", "steamID", "SteamID",
            "userId", "UserId", "userID", "UserID",
            "playerId", "PlayerId", "playerID", "PlayerID",
            "networkId", "NetworkId", "networkID", "NetworkID"
        };

        object?[] targets = new object?[] { 玩家, 玩家.data, 玩家.refs };
        foreach (object? target in targets)
        {
            if (target == null) continue;
            foreach (string name in stableDirectNames)
            {
                string text = 多人_简单标识(获取成员值按名称(target, name));
                if (text.Length == 0) continue;
                string n = name.ToLowerInvariant();
                if (n.Contains("steam")) return "steam:" + text;
                if (n.Contains("user")) return "user:" + text;
                return "player:" + text;
            }
        }

        object? view =
            获取成员值按名称(玩家, "photonView")
            ?? 获取成员值按名称(玩家, "PhotonView")
            ?? 获取成员值按名称(玩家, "view")
            ?? 获取成员值按名称(玩家, "View");

        if (view != null)
        {
            object? owner = 获取成员值按名称(view, "Owner") ?? 获取成员值按名称(view, "owner");
            if (owner != null)
            {
                foreach (string name in new string[] { "UserId", "userId" })
                {
                    string text = 多人_简单标识(获取成员值按名称(owner, name));
                    if (text.Length > 0) return "user:" + text;
                }

                // Owner IDs are weaker than a real user/platform ID, so only
                // consider them after the Photon owner UserId path above.
                foreach (object? target in targets)
                {
                    if (target == null) continue;
                    foreach (string name in new string[] { "ownerId", "OwnerId", "ownerID", "OwnerID" })
                    {
                        string text = 多人_简单标识(获取成员值按名称(target, name));
                        if (text.Length > 0) return "owner:" + text;
                    }
                }

                foreach (string name in new string[] { "ActorNumber", "actorNumber" })
                {
                    string text = 多人_简单标识(获取成员值按名称(owner, name));
                    if (text.Length > 0) return "actor:" + text;
                }
            }
            foreach (string name in new string[] { "OwnerActorNr", "ownerActorNr" })
            {
                string text = 多人_简单标识(获取成员值按名称(view, name));
                if (text.Length > 0) return "actor:" + text;
            }
            foreach (string name in new string[] { "ViewID", "viewID" })
            {
                string text = 多人_简单标识(获取成员值按名称(view, name));
                if (text.Length > 0) return "view:" + text;
            }
        }
        return "";
    }

    private static bool 多人_读取Bool(
        object? target,
        string name,
        bool fallback
    )
    {
        if (target == null)
            return fallback;

        try
        {
            object? value =
                获取成员值按名称(
                    target,
                    name
                );

            if (value is bool)
                return (bool)value;

            if (value != null)
            {
                bool parsed;

                if (
                    bool.TryParse(
                        value.ToString(),
                        out parsed
                    )
                )
                    return parsed;
            }
        }
        catch
        {
        }

        return fallback;
    }

    private static string 多人_简单字符串(
        object? value
    )
    {
        if (value == null)
            return "";

        if (value is string)
            return ((string)value).Trim();

        return "";
    }

    private static string 多人_简单标识(
        object? value
    )
    {
        if (value == null)
            return "";

        if (
            value is string ||
            value is byte ||
            value is sbyte ||
            value is short ||
            value is ushort ||
            value is int ||
            value is uint ||
            value is long ||
            value is ulong
        )
        {
            string text =
                value.ToString()
                ?.Trim()
                ?? "";

            if (
                text.Length > 0 &&
                text != "0" &&
                text != "-1"
            )
                return text;
        }

        return "";
    }

    private static void 多人_JSON数字(
        StringBuilder sb,
        float value
    )
    {
        if (
            float.IsNaN(value) ||
            float.IsInfinity(value)
        )
            value = 0f;

        sb.Append(
            value.ToString(
                "0.######",
                CultureInfo.InvariantCulture
            )
        );
    }

    private static void 多人_JSON字段(
        StringBuilder sb,
        string name,
        string value,
        bool appendComma
    )
    {
        多人_JSON字符串(sb, name);
        sb.Append(':');
        多人_JSON字符串(sb, value);

        if (appendComma)
            sb.Append(',');
    }

    private static void 多人_JSON字符串(
        StringBuilder sb,
        string? value
    )
    {
        sb.Append('"');

        string text =
            value ?? "";

        foreach (char ch in text)
        {
            switch (ch)
            {
                case '"':
                    sb.Append("\\\"");
                    break;
                case '\\':
                    sb.Append("\\\\");
                    break;
                case '\b':
                    sb.Append("\\b");
                    break;
                case '\f':
                    sb.Append("\\f");
                    break;
                case '\n':
                    sb.Append("\\n");
                    break;
                case '\r':
                    sb.Append("\\r");
                    break;
                case '\t':
                    sb.Append("\\t");
                    break;
                default:
                    if (ch < 32)
                    {
                        sb.Append("\\u");
                        sb.Append(
                            ((int)ch).ToString(
                                "X4",
                                CultureInfo.InvariantCulture
                            )
                        );
                    }
                    else
                    {
                        sb.Append(ch);
                    }
                    break;
            }
        }

        sb.Append('"');
    }
}
