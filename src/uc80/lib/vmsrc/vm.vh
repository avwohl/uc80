/* vm.vh - what the math programs call.  A .vc file includes this file;
 * only the functions that a program uses become EXTRN symbols. */

/* assembly routines of the float interpreter (see lc_vm.mac) */
extern rtn float  sqrt(float) = __vm_sqrt;
extern rtn float  ldexp(float, float) = __vm_ldexp;
extern rtn float2 frexp1(float) = __vm_frexp1;
extern rtn float2 phred(float) = __vm_phred;

/* programs of other modules */
extern vm float2 m_2sum(float, float);
extern vm float2 m_2prod(float, float);
extern vm float  m_ksin(float);
extern vm float  m_kcos(float);
extern vm float2 m_reduce(float);
extern vm float  m_kasin(float);
extern vm float2 m_expk(float);
extern vm float3 m_logk(float);
extern vm float  expm1(float);
extern vm float  log(float);
extern vm float  log1p(float);
extern vm float  atan(float);
