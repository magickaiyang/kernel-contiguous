#!/usr/bin/drgn

from drgn.helpers.linux.mm import compound_nr, decode_page_flags, page_to_pfn, pfn_to_page, PageLRU, PageReserved, PageSlab, PageUptodate, PageActive
from drgn.helpers.linux.list import list_for_each
from drgn import cast, sizeof
import sys

if '-d' in sys.argv:
       DUMP_BLOCKS = True
else:
       DUMP_BLOCKS = False

__GFP_RECLAIMABLE = 0x10

BITS_PER_LONG = 64
PAGE_SHIFT = 12
PAGE_SIZE = 1 << PAGE_SHIFT

SECTION_SIZE_BITS = 27
PFN_SECTION_SHIFT = (SECTION_SIZE_BITS - 12)
PAGES_PER_SECTION = 1 << PFN_SECTION_SHIFT

SECTIONS_PER_ROOT = (PAGE_SIZE // sizeof(prog.type("struct mem_section")))
SECTION_ROOT_MASK = SECTIONS_PER_ROOT - 1

SECTION_HAS_MEM_MAP = 2

pageblock_order = 9
#pageblock_order = 10
PB_migratetype_bits = 3
MIGRATETYPE_MASK = (1 << PB_migratetype_bits) - 1

def valid_section(ms):
       return ms.section_mem_map & 2 == 2

def pfn_to_section_nr(pfn):
       return pfn >> PFN_SECTION_SHIFT

def SECTION_NR_TO_ROOT(nr):
       return nr / SECTIONS_PER_ROOT

def __nr_to_section(nr):
       root = prog['mem_section'][SECTION_NR_TO_ROOT(nr)]
       if not root:
               return None
       return root[nr & SECTION_ROOT_MASK]

def __pfn_to_section(pfn):
       return __nr_to_section(pfn_to_section_nr(pfn))

def section_to_usemap(ms):
       return ms.usage.pageblock_flags

def get_pfnblock_flags_mask(page, pfn, mask):
       map = section_to_usemap(__pfn_to_section(pfn))
       bitidx = ((pfn & (PAGES_PER_SECTION - 1)) >> pageblock_order) * prog['NR_PAGEBLOCK_BITS']
       word_bitidx = bitidx / BITS_PER_LONG
       bitidx &= (BITS_PER_LONG - 1)
       word = map[word_bitidx]
       return (word >> bitidx) & mask

def get_pageblock_migratetype(page):
       return get_pfnblock_flags_mask(page, page_to_pfn(page), MIGRATETYPE_MASK).value_()

BUDDY = 0
LRU = 1
SUNRECLAIM = 2
SRECLAIM = 3
ZSMALLOC = 4
KMEM = 5
RESERVED = 6
UNREF = 7
OTHER = 8

typenames = [ 'buddy', 'lru', 'sunreclaim', 'sreclaim', 'zsmalloc', 'kmem', 'reserved', 'unref', 'other' ]

def page_type(p):
       if PageReserved(page):
               return RESERVED
       if (p.page_type & (0xf0000000 | 0x00000080) == 0xf0000000):
               return BUDDY
       if PageSlab(p):
               if cast('struct slab *', p).slab_cache.allocflags & __GFP_RECLAIMABLE:
                       return SRECLAIM
               else:
                       return SUNRECLAIM
       if p.mapping.value_() == prog['zsmalloc_mops'].address_of_().value_() + 2:
               return ZSMALLOC
       if PageLRU(p) or PageActive(p) or PageUptodate(p):
               return LRU
       if p.memcg_data.value_() & 2:
               return KMEM
       if not p._refcount.counter.value_():
               return UNREF
       return OTHER

migratetype_names = [ "unmovable", "movable", "reclaimable" ]
if 'MIGRATE_HIGHATOMIC' in prog: migratetype_names.append("highatomic")
if 'MIGRATE_FREE' in prog: migratetype_names.append("free")
migratetype_names += [ "cma", "isolate" ]

def dump_pageblock(first_pfn):
       if not DUMP_BLOCKS:
               return

       page = pfn_to_page(first_pfn)
       mt = get_pageblock_migratetype(page)
       print(f'dumping {migratetype_names[mt]} pageblock at {pfn}')
       i = 0
       size = 1 << pageblock_order
       while i < size:
               page = pfn_to_page(first_pfn + i)
               t = page_type(page)
               if t == BUDDY:
                       nr = 1 << page.private.value_()
               else:
                       nr = compound_nr(page).value_()
               print(f'{i} {typenames[t]} {nr}')
               i += nr

pb = 0
types = { }

slablru_in_unmovable = { 'slab': 0, 'lru': 0 }
nonslab_in_reclaimable = { 'lru': 0, 'kmem': 0, 'other': 0 }
nonlru_in_movable = { 'slab': 0, 'kmem': 0, 'other': 0 }

unmovable_with_slablru = 0
movable_with_nonlru = 0
reclaimable_with_nonslab = 0
blocks_with_nonmovable = 0

pfn = prog['min_low_pfn']
print(pfn)
#while pfn < prog['max_pfn'].value_():
while pfn <= 0x400:
       section = __pfn_to_section(pfn)
       if not valid_section(section):
               print('skipping invalid section at pfn', pfn)
               pfn += PAGES_PER_SECTION
               continue

       first_page = pfn_to_page(prog, pfn)
       mt = get_pageblock_migratetype(first_page)

       buddy = 0
       lru = 0
       reserved = 0
       slab = 0
       kmem = 0
       other = 0

       block_has_nonmovable = False

       i = 0
       size = 1 << pageblock_order
       while i < size:
               page = pfn_to_page(prog, pfn + i)
               t = page_type(page)

               if t == BUDDY:
                       nr = 1 << page.private.value_()
                       buddy += nr
                       i += nr
                       continue

               nr = compound_nr(page).value_()
               if nr > 512:
                       print(f'{pfn+i}: bogus compound size {nr}')
                       print(page)
                       sys.exit(1)

               if t == LRU:
                       lru += nr
                       # debugging
                       if mt == prog['MIGRATE_UNMOVABLE'].value_():
                               try:
                                       ops_sym = prog.symbol(page.mapping.a_ops).name
                               except:
                                       ops_sym = page
                               print(f'{pfn + i} lru page in unmovable?', decode_page_flags(page), ops_sym)
                               dump_pageblock(pfn)
                       if mt == prog['MIGRATE_RECLAIMABLE'].value_():
                               try:
                                       ops_sym = prog.symbol(page.mapping.a_ops).name
                               except:
                                       if page.mapping.value_() & 1:
                                               ops_sym = "anon mapping tag?"
                                       else:
                                               ops_sym = page.mapping
                               print(f'{pfn + i} lru page in reclaimable?', decode_page_flags(page), ops_sym)
                               dump_pageblock(pfn)
               elif t == SUNRECLAIM:
                       block_has_nonmovable = True
                       kmem += nr
                       # debugging
                       if mt == prog['MIGRATE_MOVABLE'].value_():
                               print(f'{pfn + i} unreclaimable slab page in movable?', decode_page_flags(page), cast('struct slab *', page).slab_cache.name)
                               dump_pageblock(pfn)
               elif t == SRECLAIM:
                       slab += nr
                       # debugging
                       if mt == prog['MIGRATE_MOVABLE'].value_():
                               print(f'{pfn + i} reclaimable slab page in movable?', decode_page_flags(page), cast('struct slab *', page).slab_cache.name)
                               dump_pageblock(pfn)
                       if mt == prog['MIGRATE_UNMOVABLE'].value_():
                               print(f'{pfn + i} reclaimable slab page in unmovable?', decode_page_flags(page), cast('struct slab *', page).slab_cache.name)
                               dump_pageblock(pfn)
               elif t == ZSMALLOC:
                       # not lru, but migratable
                       lru += nr
               elif t == KMEM:
                       block_has_nonmovable = True
                       kmem += nr
               elif t == RESERVED:
                       # block_has_nonmovable = True
                       reserved += nr
               elif t == UNREF:
                       buddy += nr
               elif t == OTHER:
                       block_has_nonmovable = True
                       other += nr
                       # debugging
                       if mt == prog['MIGRATE_RECLAIMABLE'].value_():
                               print(f"{pfn + i} other page in reclaimable?", decode_page_flags(page), cast('struct slab *', page).slab_cache.name if PageSlab(page) else '')

               i += nr

       if mt == prog['MIGRATE_UNMOVABLE'].value_():
               slablru_in_unmovable['slab'] += slab
               slablru_in_unmovable['lru'] += lru
               if slab or lru:
                       unmovable_with_slablru += 1
       elif mt == prog['MIGRATE_RECLAIMABLE'].value_():
               nonslab_in_reclaimable['lru'] += lru
               nonslab_in_reclaimable['kmem'] += kmem
               nonslab_in_reclaimable['other'] += other
               if lru or kmem or other:
                       reclaimable_with_nonslab += 1
       elif mt == prog['MIGRATE_MOVABLE'].value_():
               nonlru_in_movable['slab'] += slab
               nonlru_in_movable['kmem'] += kmem
               nonlru_in_movable['other'] += other
               if slab or kmem or other:
                       movable_with_nonlru += 1

       if mt in types:
               types[mt] += 1
       else:
               types[mt] = 1

       pfn += 1 << pageblock_order
       pb += 1

       if block_has_nonmovable:
               blocks_with_nonmovable += 1

for t in range(len(migratetype_names)):
       if t in types:
               print(f'{migratetype_names[t]} {types[t]}')
print(f'unmovable blocks with slab/lru pages: {unmovable_with_slablru} ({slablru_in_unmovable} pages)')
print(f'movable blocks with non-LRU pages: {movable_with_nonlru} ({nonlru_in_movable} pages)')
print(f'reclaimable blocks with non-slab pages: {reclaimable_with_nonslab} ({nonslab_in_reclaimable} pages)')
print(f'total blocks: {pb} blocks with nonmovable: {blocks_with_nonmovable}')
