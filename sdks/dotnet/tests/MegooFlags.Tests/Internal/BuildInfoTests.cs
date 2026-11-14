using System;
using System.IO;
using System.Linq;
using System.Xml.Linq;
using MegooFlags.Internal;
using Xunit;

namespace MegooFlags.Tests.Internal;

public class BuildInfoTests
{
    [Fact]
    public void VersionIsZeroOneZero()
    {
        Assert.Equal("0.1.0", BuildInfo.Version);
    }

    [Fact]
    public void VersionMatchesCsproj()
    {
        var csproj = XDocument.Load(FindCsproj());
        var version = csproj.Descendants("Version").Single().Value.Trim();
        Assert.Equal(BuildInfo.Version, version);
    }

    private static string FindCsproj()
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
        {
            var path = Path.Combine(dir.FullName, "src", "MegooFlags", "MegooFlags.csproj");
            if (File.Exists(path))
            {
                return path;
            }
        }
        throw new FileNotFoundException("src/MegooFlags/MegooFlags.csproj not found above " + AppContext.BaseDirectory);
    }
}
