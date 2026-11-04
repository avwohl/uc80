import importlib.util
PS=['/private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/famm_an/p_q1.py', '/private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/famm_an/p_putstr.py']
def _ld(p):
    s=importlib.util.spec_from_file_location('m',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
def edit(*a):
    t=a[-1]
    for p in PS:
        t=_ld(p).edit(*a[:-1],t)
    return t
