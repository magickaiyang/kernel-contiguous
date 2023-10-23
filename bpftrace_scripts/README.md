## Usage

Run the scripts with `bpftrace`. To trace mmap() and munmap() syscalls that modify the virtual address space of applications, use `collect_mmap` and `collect_munmap`. The munmap trace needs additonal parsing to handle partial unmaps, but the program has not been written yet.