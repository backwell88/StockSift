# Regenerate the committed app icon using Windows' built-in System.Drawing.
# The shape stays legible at taskbar and title-bar sizes.
$ErrorActionPreference = 'Stop'
$drawingAssemblies = @([System.Drawing.Bitmap].Assembly.Location, [System.Drawing.Color].Assembly.Location, [object].Assembly.Location)
$drawingDirectory = Split-Path ([System.Drawing.Bitmap].Assembly.Location)
foreach ($name in @('System.Private.Windows.GdiPlus.dll', 'System.Private.Windows.Core.dll', 'System.Collections.dll')) {
    $dependency = Join-Path $drawingDirectory $name
    if (Test-Path -LiteralPath $dependency) { $drawingAssemblies += $dependency }
}
Add-Type -ReferencedAssemblies $drawingAssemblies -TypeDefinition @'
using System;
using System.IO;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;

public static class StockReportIcon
{
    private static GraphicsPath RoundedSquare(float inset, float radius)
    {
        float size = 256f - 2f * inset;
        float diameter = radius * 2f;
        GraphicsPath path = new GraphicsPath();
        path.AddArc(inset, inset, diameter, diameter, 180, 90);
        path.AddArc(inset + size - diameter, inset, diameter, diameter, 270, 90);
        path.AddArc(inset + size - diameter, inset + size - diameter, diameter, diameter, 0, 90);
        path.AddArc(inset, inset + size - diameter, diameter, diameter, 90, 90);
        path.CloseFigure();
        return path;
    }

    private static Bitmap Draw(int size)
    {
        Bitmap bitmap = new Bitmap(size, size, PixelFormat.Format32bppArgb);
        using (Graphics graphics = Graphics.FromImage(bitmap))
        {
            graphics.SmoothingMode = SmoothingMode.AntiAlias;
            graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
            graphics.PixelOffsetMode = PixelOffsetMode.HighQuality;
            graphics.Clear(Color.Transparent);
            graphics.ScaleTransform(size / 256f, size / 256f);

            using (GraphicsPath tile = RoundedSquare(8f, 42f))
            using (LinearGradientBrush steel = new LinearGradientBrush(
                new RectangleF(0f, 0f, 256f, 256f),
                Color.FromArgb(255, 55, 72, 85), Color.FromArgb(255, 19, 29, 39),
                LinearGradientMode.ForwardDiagonal))
            using (Pen rim = new Pen(Color.FromArgb(210, 145, 169, 179), 4f))
            {
                graphics.FillPath(steel, tile);
                graphics.DrawPath(rim, tile);
            }

            using (GraphicsPath inset = RoundedSquare(17f, 34f))
            using (Pen innerRim = new Pen(Color.FromArgb(105, 185, 205, 211), 1.6f))
                graphics.DrawPath(innerRim, inset);

            // Brushed steel columns are secondary to the clear rising price line.
            using (SolidBrush column = new SolidBrush(Color.FromArgb(255, 95, 117, 129)))
            using (SolidBrush columnLight = new SolidBrush(Color.FromArgb(255, 145, 166, 176)))
            {
                float[] xs = { 50f, 91f, 132f, 173f };
                float[] tops = { 153f, 128f, 141f, 92f };
                for (int i = 0; i < xs.Length; i++)
                {
                    graphics.FillRectangle(column, xs[i], tops[i], 27f, 58f + 153f - tops[i]);
                    graphics.FillRectangle(columnLight, xs[i], tops[i], 27f, 4f);
                }
            }

            PointF[] trend = {
                new PointF(52f, 157f), new PointF(101f, 116f),
                new PointF(142f, 132f), new PointF(204f, 65f)
            };
            using (Pen shadow = new Pen(Color.FromArgb(95, 9, 24, 31), 24f))
            using (Pen halo = new Pen(Color.FromArgb(85, 117, 196, 210), 19f))
            using (Pen price = new Pen(Color.FromArgb(255, 134, 218, 229), 10f))
            {
                shadow.StartCap = shadow.EndCap = LineCap.Round;
                shadow.LineJoin = LineJoin.Round;
                halo.StartCap = halo.EndCap = LineCap.Round;
                halo.LineJoin = LineJoin.Round;
                price.StartCap = price.EndCap = LineCap.Round;
                price.LineJoin = LineJoin.Round;
                graphics.DrawLines(shadow, trend);
                graphics.DrawLines(halo, trend);
                graphics.DrawLines(price, trend);
            }
            using (SolidBrush point = new SolidBrush(Color.FromArgb(255, 222, 248, 250)))
                graphics.FillEllipse(point, 196f, 57f, 16f, 16f);
        }
        return bitmap;
    }

    private static byte[] Png(Bitmap bitmap)
    {
        using (MemoryStream stream = new MemoryStream())
        {
            bitmap.Save(stream, ImageFormat.Png);
            return stream.ToArray();
        }
    }

    public static void Generate(string directory)
    {
        Directory.CreateDirectory(directory);
        int[] sizes = { 16, 24, 32, 48, 64, 128, 256 };
        byte[][] images = new byte[sizes.Length][];
        for (int i = 0; i < sizes.Length; i++)
        {
            using (Bitmap bitmap = Draw(sizes[i]))
                images[i] = Png(bitmap);
        }
        File.WriteAllBytes(Path.Combine(directory, "stockreport.png"), images[images.Length - 1]);
        using (FileStream file = File.Create(Path.Combine(directory, "stockreport.ico")))
        using (BinaryWriter writer = new BinaryWriter(file))
        {
            writer.Write((ushort)0);
            writer.Write((ushort)1);
            writer.Write((ushort)sizes.Length);
            int offset = 6 + sizes.Length * 16;
            for (int i = 0; i < sizes.Length; i++)
            {
                writer.Write((byte)(sizes[i] == 256 ? 0 : sizes[i]));
                writer.Write((byte)(sizes[i] == 256 ? 0 : sizes[i]));
                writer.Write((byte)0);
                writer.Write((byte)0);
                writer.Write((ushort)1);
                writer.Write((ushort)32);
                writer.Write((uint)images[i].Length);
                writer.Write((uint)offset);
                offset += images[i].Length;
            }
            foreach (byte[] image in images) writer.Write(image);
        }
    }
}
'@

$assetDirectory = Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..\stockreport')).Path 'assets'
[StockReportIcon]::Generate($assetDirectory)
Write-Output (Join-Path $assetDirectory 'stockreport.ico')
