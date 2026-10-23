using System.Collections.Specialized;
using System.Runtime.CompilerServices;
using System.Text;
using AnimeStudio;
using Microsoft.VisualStudio.TestTools.UnitTesting;
namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class ManagedReferenceTypeTreeTests
{
    [TestMethod]
    public void ResolvesPayloadBeforeReadingNextReferenceInBothOutputFormats()
    {
        using var reader = CreateReader();
        var dump = TypeTreeHelper.ReadTypeString(reader.serializedType.m_Type, reader);
        StringAssert.Contains(dump, "int amount = 17");
        StringAssert.Contains(dump, "int amount = 29");
        Assert.AreEqual(reader.byteSize, reader.Position);
        var value = TypeTreeHelper.ReadType(reader.serializedType.m_Type, reader);
        Assert.AreEqual(17, ((OrderedDictionary)((OrderedDictionary)value["first"]!)["data"]!)["amount"]);
        Assert.AreEqual(29, ((OrderedDictionary)((OrderedDictionary)value["second"]!)["data"]!)["amount"]);
        Assert.AreEqual(reader.byteSize, reader.Position);
    }

    [TestMethod]
    public void MissingPayloadSchemaFailsInsteadOfReadingPayloadAsAnotherHeader()
    {
        using var reader = CreateReader();
        reader.assetsFile.m_RefTypes.Clear();
        Assert.ThrowsException<InvalidDataException>(() => TypeTreeHelper.ReadTypeString(reader.serializedType.m_Type, reader));
        Assert.ThrowsException<InvalidDataException>(() => TypeTreeHelper.ReadType(reader.serializedType.m_Type, reader));
    }

    private static ObjectReader CreateReader()
    {
        var stream = new MemoryStream();
        using (var writer = new BinaryWriter(stream, Encoding.UTF8, true))
        {
            foreach (var amount in new[] {17,29})
            {
                writer.Write((long)amount);
                foreach(var text in new[] {"Action/Data","Example","Gameplay"})
                {
                    var bytes=Encoding.UTF8.GetBytes(text); writer.Write(bytes.Length);writer.Write(bytes);
                    while(stream.Position % 4 != 0) writer.Write((byte)0);
                }
                writer.Write(amount);
            }
        }
        var tree = new TypeTree { m_Nodes = [new("Root","Base",0,false),new("ReferencedObject","first",1,false),new("ReferencedObject","second",1,false)] };
        var file=(SerializedFile)RuntimeHelpers.GetUninitializedObject(typeof(SerializedFile));
        file.header=new SerializedFileHeader();
        file.m_RefTypes=[new SerializedType {m_KlassName="Action/Data",m_NameSpace="Example",m_AsmName="Gameplay",m_Type=new TypeTree {m_Nodes=[new("Data","Base",0,false),new("int","amount",1,false)]}}];
        var reader=new ObjectReader(new EndianBinaryReader(stream,EndianType.LittleEndian),file,new ObjectInfo {byteSize=(uint)stream.Length,serializedType=new SerializedType {m_Type=tree}}, GameManager.GetGame(GameType.ArknightsEndfield)!);
        return reader;
    }
}
