#!/usr/bin/env drgn

"""
Scans the entire memory to find unmovable pages in the wrong pageblocks.
Remember to verify the constants are correct.
Checks for page types are racy but safe (we only risk not skipping some pages or not identifying unmovable pages).
"""

import math
from drgn import sizeof
from drgn.helpers.linux.mm import pfn_to_page

BITS_PER_LONG = 64
pageblock_nr_pages = 512  # no suitable symbols
pageblock_order = 9
MIGRATETYPE_MASK = (1 << 3) - 1
max_pfn = prog["max_pfn"].value_()
PAGE_SHIFT = 12
PAGE_SIZE = 1 << PAGE_SHIFT
SECTION_SIZE_BITS = 27
PFN_SECTION_SHIFT = SECTION_SIZE_BITS - PAGE_SHIFT
PAGES_PER_SECTION = 1 << PFN_SECTION_SHIFT
PAGE_SECTION_MASK = ~(PAGES_PER_SECTION - 1)
struct_mem_section_type = prog.type("struct mem_section")
struct_mem_section_size = sizeof(struct_mem_section_type)
SECTIONS_PER_ROOT = PAGE_SIZE // struct_mem_section_size
SECTION_ROOT_MASK = SECTIONS_PER_ROOT - 1
NR_PAGEBLOCK_BITS = prog["NR_PAGEBLOCK_BITS"].value_()
PB_migratetype_bits = 3
MIGRATETYPE_MASK = (1 << PB_migratetype_bits) - 1
PAGE_TYPE_BASE = 0xF0000000
PG_buddy = 0x00000080
MAX_ORDER = 11
MAX_ORDER_NR_PAGES = 1 << (MAX_ORDER - 1)
PG_reserved_mask = 1 << (prog["PG_reserved"].value_())
PG_head_mask = 1 << (prog["PG_head"].value_())
PG_lru_mask = 1 << (prog["PG_lru"].value_())
PG_slab_mask = 1 << (prog["PG_slab"].value_())
PAGE_MAPPING_ANON = 0x1
PAGE_MAPPING_MOVABLE = 0x2
PAGE_MAPPING_FLAGS = PAGE_MAPPING_ANON | PAGE_MAPPING_MOVABLE
SECTION_IS_ONLINE = 1 << 2
SECTION_HAS_MEM_MAP = 1 << 1
SUBSECTION_SHIFT = 21
PFN_SUBSECTION_SHIFT = SUBSECTION_SHIFT - PAGE_SHIFT
PAGES_PER_SUBSECTION = 1 << PFN_SUBSECTION_SHIFT
MIGRATE_UNMOVABLE = prog["MIGRATE_UNMOVABLE"].value_()


# https://stackoverflow.com/a/2753343
def percentile(N, percent, key=lambda x: x):
    """
    Find the percentile of a list of values.

    @parameter N - is a list of values. Note N MUST BE already sorted.
    @parameter percent - a float value from 0.0 to 1.0.
    @parameter key - optional key function to compute value from each element of N.

    @return - the percentile of the values
    """
    if not N:
        return None
    k = (len(N) - 1) * percent
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return key(N[int(k)])
    d0 = key(N[int(f)]) * (c - k)
    d1 = key(N[int(c)]) * (k - f)
    return int(d0 + d1)


def align(x, a):
    if x % a != 0:
        x += a - (x % a)
    return x


def test_bit(x, mask):
    return x & mask == mask


def pfn_to_section(pfn):
    # pfn_to_section, assuming CONFIG_SPARSEMEM, and CONFIG_SPARSEMEM_EXTREME
    pfn_section_nr = pfn >> PFN_SECTION_SHIFT
    mem_section = prog["mem_section"]
    return (
        mem_section[pfn_section_nr // SECTIONS_PER_ROOT][
            pfn_section_nr & SECTION_ROOT_MASK
        ]
    ).address_of_()


def get_pfnblock_flags_mask(page, pfn, mask):
    pfn_section = pfn_to_section(pfn)
    bitmap = pfn_section.usage.pageblock_flags
    # pfn_to_bitidx
    pfn = pfn & (PAGES_PER_SECTION - 1)
    bitidx = (pfn >> pageblock_order) * NR_PAGEBLOCK_BITS
    word_bitidx = bitidx // BITS_PER_LONG
    bitidx = bitidx & (BITS_PER_LONG - 1)
    word = bitmap[word_bitidx]
    return (word >> bitidx) & mask


def PageBuddy(page):
    return (page.page_type & (PAGE_TYPE_BASE | PG_buddy)) == PAGE_TYPE_BASE


def PageReserved(page):
    return test_bit(page.flags, PG_reserved_mask)


def PageCompound(page):
    return test_bit(page.flags, PG_head_mask) or (page.compound_head) & 1 != 0


def compound_order(page):
    if not test_bit(page.flags, PG_head_mask):
        return 0
    else:
        return page[1].compound_order.value_()


def PageLRU(page):
    return test_bit(page.flags, PG_lru_mask)


def PageSlab(page):
    return test_bit(page.flags, PG_slab_mask)


def PageMovable(page):
    return (page.mapping.value_() & PAGE_MAPPING_FLAGS) == PAGE_MAPPING_MOVABLE


def pfn_online(pfn):
    pfn_section = pfn_to_section(pfn)
    # online_section()
    if pfn_section.value_() == 0:
        return False
    if pfn_section.section_mem_map & SECTION_IS_ONLINE == 0:
        return False
    if pfn_section.section_mem_map & SECTION_HAS_MEM_MAP == 0:
        return False
    idx = (pfn & ~(PAGE_SECTION_MASK)) // PAGES_PER_SUBSECTION
    if not test_bit(pfn_section.usage.subsection_map[0], 1 << idx):
        return False
    return True


def build_list(prev_set, order):
    this_set = set()
    for pfn in prev_set:
        buddy_pfn = pfn ^ (1 << order)
        if buddy_pfn > pfn and buddy_pfn in prev_set:
            combined_pfn = buddy_pfn & pfn
            this_set.add(combined_pfn)
    return this_set

def get_boundary():
    boundary_pfn = 2 ** 64
    actual_unmovable_size = 0
    min_unmovable_size = 0
    movable_base = 0
    try:
        boundary_file = open('/proc/region_boundary', 'r')
    except IOError:
        return boundary_pfn
    lines = boundary_file.readlines()
    boundary_file.close()
    for line in lines:
        if line.startswith('movable_base'):
            split = line.split()
            movable_base = int(split[1])
        if line.startswith('actual_unmovable_size'):
            split = line.split()
            actual_unmovable_size = int(split[1])
        if line.startswith('min_unmovable_size'):
            split = line.split()
            min_unmovable_size = int(split[1])
    if actual_unmovable_size != 0 and min_unmovable_size != 0 and movable_base != 0:
        pages_expanded = (actual_unmovable_size - min_unmovable_size) / 4096
        boundary_pfn = int(movable_base + pages_expanded)
    return int(boundary_pfn)

movable_2mb_pfns = []
free_4mb_pfns = []
regular_blocks = 0
unmovable_pages_in_unmovable_blocks = (
    []
)  # count of unmovable pages in each unmovable block
movable_pages_in_unmovable_blocks = []
boundary_pfn = get_boundary()
pfn = 0
while pfn < max_pfn:
    page = pfn_to_page(prog, pfn)
    if not pfn_online(pfn):
        pfn = align(pfn + 1, MAX_ORDER_NR_PAGES)
        continue

    unmovable_pages = 0
    movable_pages = 0
    reserved_pages = 0

    block_start_pfn = pfn
    block_end_pfn = align(pfn + 1, pageblock_nr_pages)
    block_end_pfn = min(block_end_pfn, max_pfn)
    # pageblock_mt = get_pfnblock_flags_mask(page, pfn, MIGRATETYPE_MASK)

    # make the scanning faster on contiguitas kernel
    if pfn > boundary_pfn:
        regular_blocks += 1
        movable_2mb_pfns.append(block_start_pfn)
        movable_pages += 512
        pfn = block_end_pfn
        continue

    while pfn < block_end_pfn:
        page = pfn_to_page(prog, pfn)

        if PageBuddy(page):
            freepage_order = page.private.value_()  # buddy_order_unsafe(page)
            # it seems that PageBuddy is not set on every buddy page!
            if freepage_order < MAX_ORDER:  # make sure we got a valid value
                pfn += (1 << freepage_order) - 1
            pfn += 1
            # a 4MB aligned free space
            if pfn > block_end_pfn:
                assert freepage_order == 10
                regular_blocks += 1
                movable_2mb_pfns.append(block_start_pfn)
                movable_2mb_pfns.append(block_start_pfn + pageblock_nr_pages)
                free_4mb_pfns.append(block_start_pfn)
            continue

        if PageCompound(page):
            order = compound_order(page)
            if order < MAX_ORDER:
                pfn += (1 << order) - 1
                if PageLRU(page): # THP pages
                    movable_pages += min((1 << order) - 1, 511)
                    assert order == 9
                    movable_2mb_pfns.append(block_start_pfn)
                else:
                    unmovable_pages += min((1 << order) - 1, 511)
            pfn += 1
            if PageLRU(page):
                movable_pages += 1
            else:
                unmovable_pages += 1
            # a 4MB compound page
            if pfn > block_end_pfn:
                assert order == 10
                unmovable_pages_in_unmovable_blocks.append(512)
                movable_pages_in_unmovable_blocks.append(0)
            continue

        if PageReserved(page):
            reserved_pages += 1
            pfn += 1
            continue

        if PageLRU(page) or PageMovable(page):
            movable_pages += 1
            pfn += 1
            continue

        # an unmovable page
        unmovable_pages += 1
        pfn += 1

    # build our own list of potential pages of sizes 2MB to 1GB
    # for every size 2MB upwards, remember to add in 1GB hugetlb pages!

    # ignore page blocks filled with reserved pages -
    # they cannot be used for anything anyways
    if reserved_pages == 512:
        pass
    elif unmovable_pages == 0:
        regular_blocks += 1
        movable_2mb_pfns.append(block_start_pfn)
    else:
        unmovable_pages_in_unmovable_blocks.append(unmovable_pages)
        movable_pages_in_unmovable_blocks.append(movable_pages)

total_unmovable_pages = sum(unmovable_pages_in_unmovable_blocks)
assert len(movable_pages_in_unmovable_blocks) == len(
    unmovable_pages_in_unmovable_blocks
)

free_pages_in_unmovable_blocks = []
for i in range(len(unmovable_pages_in_unmovable_blocks)):
    free_pages_in_this_block = (
        512
        - movable_pages_in_unmovable_blocks[i]
        - unmovable_pages_in_unmovable_blocks[i]
    )
    assert free_pages_in_this_block >= 0
    free_pages_in_unmovable_blocks.append(free_pages_in_this_block)

free_pages_in_unmovable_blocks.sort()
movable_pages_in_unmovable_blocks.sort()
unmovable_pages_in_unmovable_blocks.sort()
p50_free_pages = percentile(free_pages_in_unmovable_blocks, 0.50)
p99_free_pages = percentile(free_pages_in_unmovable_blocks, 0.99)
p50_movable_pages = percentile(movable_pages_in_unmovable_blocks, 0.50)
p99_movable_pages = percentile(movable_pages_in_unmovable_blocks, 0.99)
p50_unmovable_pages = percentile(unmovable_pages_in_unmovable_blocks, 0.50)
p99_unmovable_pages = percentile(unmovable_pages_in_unmovable_blocks, 0.99)

# assemble the movable blocks from 2MB upwards to 1GB
print('movable_order 9', len(movable_2mb_pfns))
movable_blocks_by_size = [set(movable_2mb_pfns)]
for i in range(10, 19):
    sz_set = build_list(movable_blocks_by_size[i-10], i)
    movable_blocks_by_size.append(sz_set)
    print('movable_order', i, len(sz_set))

# assemble free blocks from 4MB upwards to 1GB
print('free_order 10', len(free_4mb_pfns))
free_blocks_by_size = [set(free_4mb_pfns)]
for i in range(11, 19):
    sz_set = build_list(free_blocks_by_size[i-11], i)
    free_blocks_by_size.append(sz_set)
    print('free_order', i, len(sz_set))

print(
    "regular_blocks,unmovable_blocks,unmovable_percentage,unmovable_pages,p50_free_pages,p99_free_pages,"
    "p50_movable_pages,p99_movable_pages,p50_unmovable_pages,p99_unmovable_pages,"
)

unmovable_blocks = len(unmovable_pages_in_unmovable_blocks)

print(
    regular_blocks,
    unmovable_blocks,
    # len(unmovable_pages_in_unmovable_blocks),
    unmovable_blocks / (float(regular_blocks) + float(unmovable_blocks)),
    total_unmovable_pages,
    p50_free_pages,
    p99_free_pages,
    p50_movable_pages,
    p99_movable_pages,
    p50_unmovable_pages,
    p99_unmovable_pages,
    sep=",",
)
