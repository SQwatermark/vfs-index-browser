using Microsoft.VisualStudio.TestTools.UnitTesting;
using SixLabors.ImageSharp;
using SixLabors.ImageSharp.PixelFormats;
using Vfs.Endfield.Extensions;

namespace Vfs.UnityWorker.Tests;

[TestClass]
public sealed class BundlePreviewMediaExporterTests
{
    [TestMethod]
    public void RestoreSpriteCanvas_PlacesTrimmedPixelsOnTheLogicalCanvas()
    {
        using var content = new Image<Bgra32>(2, 3, Color.Red);
        using var canvas = BundlePreviewMediaExporter.RestoreSpriteCanvas(
            content,
            width: 6,
            height: 7,
            offsetX: 1,
            offsetY: 2);

        Assert.AreEqual(6, canvas.Width);
        Assert.AreEqual(7, canvas.Height);
        Assert.AreEqual(content[0, 0], canvas[1, 2]);
        Assert.AreEqual(content[1, 2], canvas[2, 4]);
        Assert.AreEqual(new Bgra32(0, 0, 0, 0), canvas[0, 0]);
        Assert.AreEqual(new Bgra32(0, 0, 0, 0), canvas[3, 4]);
    }

    [TestMethod]
    public void RestoreSpriteCanvas_RejectsContentOutsideTheLogicalCanvas()
    {
        using var content = new Image<Bgra32>(4, 4, Color.Red);
        Assert.ThrowsException<InvalidDataException>(() =>
            BundlePreviewMediaExporter.RestoreSpriteCanvas(
                content,
                width: 5,
                height: 5,
                offsetX: 2,
                offsetY: 0));
    }
}
