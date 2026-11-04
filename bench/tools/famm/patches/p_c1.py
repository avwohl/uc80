import importlib.util
PS=['/private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/famm_an/p_crt.py', '/private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/famm_an/p_gap.py']
def _ld(p):
    s=importlib.util.spec_from_file_location('m',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
def edit(t):
    for p in PS:
        t=_ld(p).edit(t)
    return t
