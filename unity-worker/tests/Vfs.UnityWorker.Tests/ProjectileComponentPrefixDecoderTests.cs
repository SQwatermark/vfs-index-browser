using Microsoft.VisualStudio.TestTools.UnitTesting;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class ProjectileComponentPrefixDecoderTests
{
    [TestMethod]
    public void RejectsEntryWithDifferentRuntimeType()
    {
        var entry = new ManagedReferenceEntry(
            1,
            "AbilitySystemData",
            "Beyond.Gameplay.Core",
            "Gameplay.Beyond",
            0,
            0,
            0,
            false);

        var exception = Assert.ThrowsException<InvalidDataException>(() =>
            ProjectileComponentPrefixDecoder.Decode([], entry));

        StringAssert.Contains(exception.Message, "ProjectileComponentData");
    }
}
