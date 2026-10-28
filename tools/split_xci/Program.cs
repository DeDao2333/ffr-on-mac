using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using LibHac.Common;
using LibHac.Common.Keys;
using LibHac.Fs;
using LibHac.Fs.Fsa;
using LibHac.FsSystem;
using LibHac.Tools.Fs;
using LibHac.Tools.FsSystem;
using LibHac.Tools.FsSystem.NcaUtils;

// Lists the NCAs and CNMT content of a combined XCI; with an output folder, writes one NSP per CNMT
// (base / update / DLC). The XCI is opened read-only. Keys come from the user's own prod.keys.
if (args.Length < 2) throw new ArgumentException("usage: split_xci <keys> <xci> [split-output-folder]");
var keys = ExternalKeyReader.ReadKeyFile(args[0]);
var xci = new Xci(keys, new LocalStorage(args[1], FileAccess.Read));
IFileSystem secure = xci.OpenPartition(XciPartitionType.Secure);
var entries = secure.EnumerateEntries("*", SearchOptions.Default).ToArray();
var groups = new List<(ulong title, uint version, byte type, List<string> names)>();
foreach (var entry in entries.Where(e => e.Name.EndsWith(".nca", StringComparison.OrdinalIgnoreCase))) {
    using var file = new UniqueRef<IFile>();
    secure.OpenFile(ref file.Ref, entry.FullPath.ToU8Span(), OpenMode.Read).ThrowIfFailure();
    var nca = new Nca(keys, file.Release().AsStorage());
    Console.WriteLine($"NCA {entry.Name} title={nca.Header.TitleId:x16} type={nca.Header.ContentType} size={entry.Size}");
    if (nca.Header.ContentType != NcaContentType.Meta) continue;
    var fs = nca.OpenFileSystem(NcaSectionType.Data, IntegrityCheckLevel.ErrorOnInvalid);
    foreach (var meta in fs.EnumerateEntries("*.cnmt", SearchOptions.RecurseSubdirectories)) {
        using var cnmt = new UniqueRef<IFile>();
        fs.OpenFile(ref cnmt.Ref, meta.FullPath.ToU8Span(), OpenMode.Read).ThrowIfFailure();
        cnmt.Get.GetSize(out long size).ThrowIfFailure();
        byte[] data = new byte[size];
        cnmt.Get.Read(out _, 0, data, ReadOption.None).ThrowIfFailure();
        ulong title = BinaryPrimitives.ReadUInt64LittleEndian(data);
        uint version = BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(8));
        byte type = data[12];
        int extra = BinaryPrimitives.ReadUInt16LittleEndian(data.AsSpan(14));
        int count = BinaryPrimitives.ReadUInt16LittleEndian(data.AsSpan(16));
        var names = new List<string> { entry.Name };
        Console.WriteLine($"  CNMT title={title:x16} version={version} type=0x{type:x2} count={count}");
        for (int i = 0; i < count; i++) {
            int offset = 0x20 + extra + i * 0x38;
            string id = Convert.ToHexString(data.AsSpan(offset + 32, 16)).ToLowerInvariant();
            string name = entries.Single(e => e.Name.StartsWith(id, StringComparison.OrdinalIgnoreCase)).Name;
            names.Add(name);
            Console.WriteLine($"    content {name} type={data[offset + 54]}");
        }
        groups.Add((title, version, type, names));
    }
}
if (args.Length < 3) return;
Directory.CreateDirectory(args[2]);
foreach (var group in groups) {
    string label = group.type == 0x80 ? "base" : group.type == 0x81 ? "update" : "dlc";
    string dest = System.IO.Path.Combine(args[2], $"FFR_{label} [{group.title:X16}][v{group.version}].nsp");
    var files = group.names.Select(n => entries.Single(e => e.Name == n)).ToArray();
    byte[] strings = Encoding.UTF8.GetBytes(string.Join('\0', files.Select(e => e.Name)) + "\0");
    int padded = (strings.Length + 15) & ~15;
    long expectedSize = 16 + files.Length * 24 + padded + files.Sum(e => e.Size);
    if (File.Exists(dest) && new FileInfo(dest).Length == expectedSize) {
        Console.WriteLine($"REUSED {dest}"); continue;
    }
    string temp = dest + ".partial";
    using (var output = new FileStream(temp, FileMode.Create, FileAccess.Write)) {
        using var writer = new BinaryWriter(output, Encoding.UTF8, true);
        writer.Write(Encoding.ASCII.GetBytes("PFS0")); writer.Write(files.Length); writer.Write(padded); writer.Write(0);
        long offset = 0; int nameOffset = 0;
        foreach (var file in files) {
            writer.Write(offset); writer.Write(file.Size); writer.Write(nameOffset); writer.Write(0);
            offset += file.Size; nameOffset += Encoding.UTF8.GetByteCount(file.Name) + 1;
        }
        writer.Write(strings); writer.Write(new byte[padded - strings.Length]); writer.Flush();
        byte[] buffer = new byte[8 * 1024 * 1024];
        foreach (var entry in files) {
            Console.WriteLine($"COPY {entry.Name} ({entry.Size} bytes)");
            using var file = new UniqueRef<IFile>();
            secure.OpenFile(ref file.Ref, entry.FullPath.ToU8Span(), OpenMode.Read).ThrowIfFailure();
            for (long pos = 0; pos < entry.Size;) {
                int want = (int)Math.Min(buffer.Length, entry.Size - pos);
                file.Get.Read(out long got, pos, buffer.AsSpan(0, want), ReadOption.None).ThrowIfFailure();
                if (got == 0) throw new EndOfStreamException(entry.Name);
                output.Write(buffer, 0, (int)got); pos += got;
            }
        }
    }
    if (new FileInfo(temp).Length != expectedSize) throw new IOException("NSP size mismatch");
    File.Move(temp, dest, true);
    Console.WriteLine($"WROTE {dest} ({expectedSize} bytes)");
}
