# RTK - Rust Token Killer (Google Antigravity)

**Usage**: Token-optimized CLI proxy for shell commands.

## Rule

Always prefix shell commands with `rtk` (or its absolute path `/Users/macbbook/.local/bin/rtk` if not in PATH) to minimize token consumption.

Examples:

```bash
rtk git status
rtk cargo test
rtk ls src/
rtk grep "pattern" src/
rtk find "*.rs" .
rtk docker ps
rtk gh pr list
```

If `rtk` command is not found in the current session's PATH:
Use `/Users/macbbook/.local/bin/rtk` directly:
```bash
/Users/macbbook/.local/bin/rtk git status
/Users/macbbook/.local/bin/rtk ls
```

## Meta Commands

```bash
rtk gain              # Show token savings
rtk gain --history    # Command history with savings
rtk discover          # Find missed RTK opportunities
rtk proxy <cmd>       # Run raw (no filtering, for debugging)
```

## Why

RTK filters and compresses command output before it reaches the LLM context, saving 60-90% tokens on common operations. Always use `rtk <cmd>` instead of raw commands.
