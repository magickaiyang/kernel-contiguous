## Usage

Run the scripts with `drgn`, a programmable debugger developed at Meta for debugging the Linux kernel.

Check [here](https://drgn.readthedocs.io/en/latest/) for how to install drgn. Note that you will also need the kernel debug symbols installed.

Both scripts scan the physical memory and report various statistics of unmovable allocations. They are written by me (`scan_unmovable.py`) and Johannes Weiner (`scan_unmovable_johannes.py`) at different points in time.