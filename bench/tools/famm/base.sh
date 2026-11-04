#!/bin/bash
. /private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/famm_an/env.sh
export AB_OUT=$A/ab
cd $W/bench/tools/famm
for p in "$@"; do python3 ab.py $p --tag base 2>&1 | grep -v '^$' ; done
