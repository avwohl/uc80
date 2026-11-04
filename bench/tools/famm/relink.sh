#!/bin/bash
# relink.sh MAC [extra.rel...] : assemble MAC, link with libs, print image size (BSS_START-0x100)
cd /private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/wt/famlan
export PYTHONPATH=$PWD/src:$PWD/../bugfix6core/src
lib=$(uc80 --print-lib-dir)
m=$1; shift
d=$(mktemp -d /private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/faml_an/rl.XXXXXX)
um80 $m -o $d/p.rel >/dev/null && ul80 $d/p.rel "$@" $lib/libc.lib $lib/runtime.lib -o $d/p.com -S $d/p.sym >/dev/null || { echo link fail; exit 1; }
b=$(grep -i " __bss_start" $d/p.sym | awk '{print $1}')
echo "image $((0x$b - 0x100))  ($d)"
