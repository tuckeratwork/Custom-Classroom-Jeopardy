#include <stdio.h>
#include <string.h>

__attribute__((noinline))
void copy_label(const char *student_text)
{
    char buffer[32];

    puts("Inside copy_label()");
    printf("  buffer starts at %p\n", (void *)buffer);

    /* Intentionally unsafe for this local, controlled teaching lab. */
    strcpy(buffer, student_text);

    printf("  first 32 bytes: %.32s\n", buffer);
}

int main(int argc, char **argv)
{
    if (argc != 2) {
        fprintf(stderr, "Usage: %s TEXT\n", argv[0]);
        return 1;
    }

    copy_label(argv[1]);
    puts("copy_label() returned normally.");
    return 0;
}
