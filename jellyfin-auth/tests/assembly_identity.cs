// Disposable CI metadata inspection only. Never load or execute the target assemblies.
using System.Diagnostics;
using System.Reflection;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;

static AssemblyName ReadName(string path) => AssemblyName.GetAssemblyName(path);
static string Key(AssemblyName name) => Convert.ToHexString(name.GetPublicKeyToken() ?? []);
static string Culture(AssemblyName name) => name.CultureName ?? "";
static Dictionary<string, AssemblyName> References(string path)
{
    using var stream = File.OpenRead(path);
    using var pe = new PEReader(stream);
    var metadata = pe.GetMetadataReader();
    return metadata.AssemblyReferences
        .Select(handle => metadata.GetAssemblyReference(handle).GetAssemblyName())
        .ToDictionary(reference => reference.Name!, StringComparer.OrdinalIgnoreCase);
}

if (args.Length < 2)
    throw new ArgumentException("Expected show, version, file-version, or verify and assembly path.");

var original = ReadName(args[1]);
switch (args[0])
{
    case "show":
        Console.WriteLine(original.FullName);
        return;
    case "version":
        Console.WriteLine(original.Version);
        return;
    case "file-version":
        var info = FileVersionInfo.GetVersionInfo(args[1]);
        Console.WriteLine(new Version(info.FileMajorPart, info.FileMinorPart,
            info.FileBuildPart, info.FilePrivatePart));
        return;
    case "verify" when args.Length == 4:
        var candidate = ReadName(args[2]);
        Console.WriteLine("Official identity: " + original.FullName);
        Console.WriteLine("Patched identity:  " + candidate.FullName);
        if (!string.Equals(original.FullName, candidate.FullName, StringComparison.Ordinal))
            throw new InvalidOperationException("Assembly identity mismatch; refusing replacement.");

        var originalRefs = References(args[1]);
        var candidateRefs = References(args[2]);
        foreach (var required in candidateRefs.Values.OrderBy(x => x.Name))
        {
            if (originalRefs.TryGetValue(required.Name!, out var previous) &&
                string.Equals(previous.FullName, required.FullName, StringComparison.Ordinal))
                continue;
            var installedPath = Path.Combine(args[3], required.Name + ".dll");
            if (!File.Exists(installedPath))
                throw new InvalidOperationException("New or changed dependency absent: " + required.FullName);
            var installed = ReadName(installedPath);
            Console.WriteLine("Changed reference: " + required.FullName + " -> " + installed.FullName);
            if (!string.Equals(required.Name, installed.Name, StringComparison.OrdinalIgnoreCase) ||
                !string.Equals(Key(required), Key(installed), StringComparison.Ordinal) ||
                !string.Equals(Culture(required), Culture(installed), StringComparison.OrdinalIgnoreCase) ||
                installed.Version is null || required.Version is null ||
                installed.Version.CompareTo(required.Version) < 0)
                throw new InvalidOperationException("Installed dependency is incompatible: " + required.FullName);
        }
        Console.WriteLine("JELLYFIN_PATCHED_ASSEMBLY_IDENTITY_AND_REFERENCES=PASS");
        return;
    default:
        throw new ArgumentException("Unrecognized assembly metadata operation.");
}
