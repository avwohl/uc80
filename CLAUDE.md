Write in the python language
Softare below are sister projects.
See ../projectname for documentation
For the programming languages there
are pdfs of the language refrence
(plm, m80, l80). DO NOT READ DIRECTLY you will loop

See docs/paid/ISO+IEC+9899-2024.txt for the c standard we need to implement

Do NOT implement K&R-style (old-style) function definitions where parameters
are declared after the closing parenthesis. These were removed in C23 (our
target standard). Only prototype-style function declarations are supported.

WARNING: Do not attempt to read large PDF files (like ISO C standards).
Reading PDFs over a few pages causes the assistant to hang/loop indefinitely.
Use web searches or summarized documentation instead. 
In the path is our uplm80 compiler
In the path is um80 assembler (asseble mac output of uplm80)
In the path is ul80 linker
Not in the path is ../cpmemu/src/cpmemu build
it is linux program that runs cp/m programs and translates
system calls.  If we made 80un.com then running cpmemu 80un.com args to 80un
you could test 80un
All of the above are in github on this accounts.

External test suites are located at:
- ../external/*test* (various test files)
- ../external/z88dk (z88dk test suite)
