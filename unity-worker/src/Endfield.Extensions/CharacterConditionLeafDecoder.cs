namespace Vfs.Endfield.Extensions;

/// <summary>Verified Unity-serialized leaves only; unknown types remain raw, malformed known types fail.</summary>
public static class CharacterConditionLeafDecoder
{
    public static IReadOnlyDictionary<string, object?>? Decode(byte[] raw, ManagedReferenceEntry entry)
    {
        if (entry.AssemblyName != "Gameplay.Beyond") return null;
        var spell = entry.ClassName == "CheckSpellInflictionType/Data"
            && entry.Namespace == "Beyond.Gameplay.Core.Conditions";
        var compare = entry.ClassName == "CompareFloat/Data" && entry.Namespace == "Beyond.Gameplay.Core";
        if (!spell && !compare) return null;
        var r = new ManagedReferencePayloadReader(raw, entry.DataOffset, entry.DataLength);
        var data = new Dictionary<string, object?>
        {
            ["isEnable"] = r.ReadBool32("isEnable"),
            ["priorityLevel"] = r.ReadInt32("priorityLevel"),
            ["priorityOffset"] = r.ReadInt32("priorityOffset"),
            ["serverActionIndex"] = r.ReadInt32("serverActionIndex"),
        };
        if (spell)
        {
            data["mask"] = r.ReadInt32("mask");
            data["savedKey"] = r.ReadAlignedUtf8String("savedKey");
        }
        else
        {
            // Unity declaration order, NOT MemoryPack member order. Serialized value is float32.
            data["valueA"] = ReadNumber(r, "valueA");
            data["compare"] = r.ReadInt32("compare");
            data["valueB"] = ReadNumber(r, "valueB");
        }
        r.EnsureComplete();
        return data;
    }

    private static Dictionary<string, object?> ReadNumber(ManagedReferencePayloadReader r, string path) => new()
    {
        ["useKey"] = r.ReadBool32(path + ".useKey"),
        ["value"] = r.ReadFloat(path + ".value"),
        ["key"] = r.ReadAlignedUtf8String(path + ".key"),
    };
}
