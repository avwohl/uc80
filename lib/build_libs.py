#!/usr/bin/env python3
"""Build runtime.lib and libc.lib from split modules.

Usage:
    python lib/build_libs.py          # build both
    python lib/build_libs.py runtime   # build runtime.lib only
    python lib/build_libs.py libc      # build libc.lib only
"""
import os
import sys
import subprocess
import glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RT_DIR = os.path.join(SCRIPT_DIR, "rt")
LC_DIR = os.path.join(SCRIPT_DIR, "lc")

def run(cmd, **kwargs):
    """Run a command, returning (success, output)."""
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    output = result.stdout + result.stderr
    return result.returncode == 0, output

def assemble_dir(src_dir, verbose=True):
    """Assemble all .mac files in a directory to .rel files."""
    mac_files = sorted(glob.glob(os.path.join(src_dir, "*.mac")))
    if not mac_files:
        print(f"  No .mac files found in {src_dir}")
        return False

    all_ok = True
    for mac_file in mac_files:
        rel_file = mac_file.replace(".mac", ".rel")
        name = os.path.basename(mac_file)
        ok, output = run(["um80", mac_file, "-o", rel_file])
        if not ok:
            print(f"  FAIL: {name}")
            for line in output.strip().split("\n"):
                if "Error" in line:
                    print(f"    {line}")
            all_ok = False
        elif verbose:
            # Extract code/data sizes from output
            for line in output.strip().split("\n"):
                if "Code segment:" in line or "Data segment:" in line:
                    pass  # quiet
            print(f"  OK: {name}")
    return all_ok

def build_lib(lib_path, rel_dir):
    """Create a .lib archive from all .rel files in a directory."""
    rel_files = sorted(glob.glob(os.path.join(rel_dir, "*.rel")))
    if not rel_files:
        print(f"  No .rel files found in {rel_dir}")
        return False

    # Remove old lib first
    if os.path.exists(lib_path):
        os.remove(lib_path)

    cmd = ["ulib80", "-c", lib_path] + rel_files
    ok, output = run(cmd)
    if ok:
        print(f"  Created {os.path.basename(lib_path)}")
        for line in output.strip().split("\n"):
            if "Modules:" in line or "Symbols:" in line:
                print(f"    {line.strip()}")
    else:
        print(f"  FAIL creating {os.path.basename(lib_path)}")
        print(output)
    return ok

def build_runtime():
    """Build runtime.lib from lib/rt/ modules."""
    print("Building runtime.lib...")
    print("  Assembling modules...")
    if not assemble_dir(RT_DIR):
        print("  Assembly errors - fix before building library")
        return False
    return build_lib(os.path.join(SCRIPT_DIR, "runtime.lib"), RT_DIR)

def build_libc():
    """Build libc.lib from lib/lc/ modules."""
    print("Building libc.lib...")
    print("  Assembling modules...")
    if not assemble_dir(LC_DIR):
        print("  Assembly errors - fix before building library")
        return False
    return build_lib(os.path.join(SCRIPT_DIR, "libc.lib"), LC_DIR)

def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else ["runtime", "libc"]
    ok = True
    for target in targets:
        if target == "runtime":
            ok = build_runtime() and ok
        elif target == "libc":
            ok = build_libc() and ok
        else:
            print(f"Unknown target: {target}")
            ok = False
    if not ok:
        sys.exit(1)
    print("\nBuild complete.")

if __name__ == "__main__":
    main()
