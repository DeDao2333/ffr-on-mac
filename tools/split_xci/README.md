# split_xci

Splits a combined XCI into one NSP per content meta: base game, update, DLC. `build_app.py` runs it for you; see [Install · What it does](../../docs/install.md#what-it-does) for why it is needed.

```sh
dotnet run -c Release -- <prod.keys> <game.xci>                 # list the content only
dotnet run -c Release -- <prod.keys> <game.xci> <output folder>  # split
```

- Needs the .NET 10 SDK; uses [LibHac](https://github.com/Thealexbarney/LibHac) 0.19.0 from NuGet
- Contains no keys: they come from your own `prod.keys`
- Opens the XCI read-only. An NSP that already exists with the right size is reused; a new one is written to `.partial` and renamed only after its size checks out
