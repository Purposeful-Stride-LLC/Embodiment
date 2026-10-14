/* LD_PRELOAD open tracer (OPEN-18a test). Logs "flags<TAB>path" for every open/open64/openat/
   openat64/creat/fopen whose path contains $OPENTRACE_MATCH, to $OPENTRACE_LOG. Test-only. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/types.h>
static void logit(const char *how, const char *p, int flags) {
    const char *m = getenv("OPENTRACE_MATCH"), *l = getenv("OPENTRACE_LOG");
    if (!p || !l || (m && !strstr(p, m))) return;
    static int (*ro)(const char*, int, ...) = 0;
    if (!ro) ro = dlsym(RTLD_NEXT, "open");
    int fd = ro(l, O_WRONLY | O_APPEND | O_CREAT, 0644);
    if (fd < 0) return;
    char b[4200]; int n = snprintf(b, sizeof b, "%s\t%d\t%s\n", how, flags, p);
    if (n > 0) { ssize_t w = write(fd, b, n); (void)w; }
    close(fd);
}
#define GETMODE mode_t mode = 0; if (flags & (O_CREAT | O_TMPFILE)) { va_list a; va_start(a, flags); mode = va_arg(a, int); va_end(a); }
int open(const char *p, int flags, ...) { GETMODE; static int (*f)(const char*, int, ...); if (!f) f = dlsym(RTLD_NEXT, "open"); logit("open", p, flags); return f(p, flags, mode); }
int open64(const char *p, int flags, ...) { GETMODE; static int (*f)(const char*, int, ...); if (!f) f = dlsym(RTLD_NEXT, "open64"); logit("open64", p, flags); return f(p, flags, mode); }
int openat(int d, const char *p, int flags, ...) { GETMODE; static int (*f)(int, const char*, int, ...); if (!f) f = dlsym(RTLD_NEXT, "openat"); logit("openat", p, flags); return f(d, p, flags, mode); }
int openat64(int d, const char *p, int flags, ...) { GETMODE; static int (*f)(int, const char*, int, ...); if (!f) f = dlsym(RTLD_NEXT, "openat64"); logit("openat64", p, flags); return f(d, p, flags, mode); }
int creat(const char *p, mode_t m) { static int (*f)(const char*, mode_t); if (!f) f = dlsym(RTLD_NEXT, "creat"); logit("creat", p, O_CREAT|O_WRONLY|O_TRUNC); return f(p, m); }
FILE *fopen(const char *p, const char *md) { static FILE *(*f)(const char*, const char*); if (!f) f = dlsym(RTLD_NEXT, "fopen"); logit("fopen", p, strpbrk(md, "wa+") ? O_RDWR : O_RDONLY); return f(p, md); }
