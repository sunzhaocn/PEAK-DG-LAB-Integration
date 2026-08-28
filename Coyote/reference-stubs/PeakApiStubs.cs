// Compile-time PEAK API reference declarations for GitHub Actions only.
//
// IMPORTANT:
// - This file contains declarations/signatures only; it contains no PEAK game
//   implementation code or assets.
// - The produced Assembly-CSharp.dll is used only while compiling Coyote.dll in
//   CI and MUST NOT be included in the portable release.
// - At runtime Coyote binds to PEAK's real Assembly-CSharp.dll loaded by the
//   game/BepInEx process.
//
// Signatures below are intentionally limited to members directly referenced by
// Coyote source. Keeping this surface small makes API drift visible as a build
// failure instead of silently shipping an incompatible plugin.

using System;
using UnityEngine;

public class Character : MonoBehaviour
{
    public static Character localCharacter = null!;

    public CharacterData data = null!;
    public CharacterRefs refs = null!;

    // PEAK exposes this as a property rather than a field.
    public Player player => null!;

    public float GetMaxStamina() => 0f;

    [Serializable]
    public class CharacterRefs
    {
        public CharacterItems items = null!;
        public CharacterAfflictions afflictions = null!;
    }
}

public class CharacterData : MonoBehaviour
{
    public float currentStamina { get; set; }

    public float extraStamina;
    public bool dead;
    public bool passedOut;
    public bool isClimbing;
    public bool isGrounded;
    public bool isCrouching;
    public Vector3 groundPos;
    public Vector3 lookDirection;
    public float sinceClimb;
    public ClimbHandle currentClimbHandle = null!;
    public float currentHeadHeight;
    public float targetHeadHeight;
    public float targetHipHeight;
    public Item currentItem = null!;
}

public class CharacterItems : MonoBehaviour
{
}

public class CharacterAfflictions : MonoBehaviour
{
    public float[] currentStatuses = Array.Empty<float>();

    // Only Injury is referenced by name in Coyote. The runtime Enum.GetValues
    // call uses PEAK's real enum and therefore sees the complete game enum.
    public enum STATUSTYPE
    {
        Injury = 0
    }
}

public class Player : MonoBehaviour
{
    public ItemSlot[] itemSlots = Array.Empty<ItemSlot>();
    public BackpackSlot backpackSlot = null!;
}

[Serializable]
public class ItemSlot
{
    public Item prefab = null!;
    public ItemInstanceData data = null!;
    public byte itemSlotID;

    public virtual bool IsEmpty() => prefab == null;
}

public class BackpackSlot : ItemSlot
{
}

public class Item : MonoBehaviour
{
}

public class ClimbHandle : MonoBehaviour
{
}

public enum DataEntryKey : byte
{
    INVALID = 0,
    BackpackData = 6
}

public abstract class DataEntryValue
{
}

public class BackpackData : DataEntryValue
{
    public ItemSlot[] itemSlots = Array.Empty<ItemSlot>();
}

public class ItemInstanceData
{
    public bool TryGetDataEntry<T>(DataEntryKey key, out T value)
        where T : DataEntryValue
    {
        value = default!;
        return false;
    }
}

public enum Segment : byte
{
    Beach = 0,
    Tropics = 1,
    Alpine = 2,
    Caldera = 3,
    TheKiln = 4,
    Peak = 5
}

public class Biome
{
    public enum BiomeType
    {
        Unknown = 0
    }
}

public class MapHandler : MonoBehaviour
{
    public Segment GetCurrentSegment() => default;
    public Biome.BiomeType GetCurrentBiome() => default;
}

public class MountainProgressHandler : MonoBehaviour
{
    public ProgressPoint[] progressPoints = Array.Empty<ProgressPoint>();

    [Serializable]
    public class ProgressPoint
    {
        public string title = string.Empty;
    }
}
