#!/bin/bash
# qwtest.sh "DEFS" qsortmodule.mac
cd /private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/wt/famlan
export PYTHONPATH=$PWD/src:$PWD/../bugfix6core/src
Q=../../faml_an/qs; W=$Q/_tw; mkdir -p $W
lib=$(uc80 --print-lib-dir)
od=(); for d in $1; do od+=(-D $d); done
uc80 $Q/qwtest.c -o $W/t.mac --no-register-cc && um80 $W/t.mac -o $W/t.rel && um80 $Q/adapter.mac -o $W/ad.rel && um80 "${od[@]}" ${2:-$Q/qw3.mac} -o $W/q.rel && ul80 $W/t.rel $W/ad.rel $W/q.rel $lib/libc.lib $lib/runtime.lib -o $W/t.com || exit 1
cd $W && ~/src/cpmemu/src/cpmemu t.com | tail -5
