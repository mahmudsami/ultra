
import sys
import re
import math
from itertools import groupby 
# import parasail
import edlib

from collections import namedtuple, defaultdict

from modules import colinear_solver 
from modules import help_functions


mam = namedtuple('Mam', ['x', 'y', 'c', 'd', 'val', "min_segment_length", "exon_id", "ref_chr_id"])
globals()[mam.__name__] = mam # Global needed for multiprocessing


def cigar_to_seq(cigar, query, ref):
    cigar_tuples = []
    result = re.split(r'[=DXSMI]+', cigar)
    i = 0
    for length in result[:-1]:
        i += len(length)
        type_ = cigar[i]
        i += 1
        cigar_tuples.append((int(length), type_ ))

    r_index = 0
    q_index = 0
    q_aln = []
    r_aln = []
    for length_ , type_ in cigar_tuples:
        if type_ == "=" or type_ == "X":
            q_aln.append(query[q_index : q_index + length_])
            r_aln.append(ref[r_index : r_index + length_])

            r_index += length_
            q_index += length_
        
        elif  type_ == "I":
            # insertion w.r.t. reference
            r_aln.append('-' * length_)
            q_aln.append(query[q_index: q_index + length_])
            #  only query index change
            q_index += length_

        elif type_ == 'D':
            # deletion w.r.t. reference
            r_aln.append(ref[r_index: r_index + length_])
            q_aln.append('-' * length_)
            #  only ref index change
            r_index += length_
        
        else:
            print("error")
            print(cigar)
            sys.exit()

    return  "".join([s for s in q_aln]), "".join([s for s in r_aln]), cigar_tuples

def cigar_to_accuracy(cigar_string):
    cigar_tuples = []
    result = re.split(r'[=DXSMI]+', cigar_string)
    i = 0
    for length in result[:-1]:
        i += len(length)
        type_ = cigar_string[i]
        i += 1
        cigar_tuples.append((int(length), type_ ))
    # print(cigar_tuples)
    aln_length = 0
    matches = 0
    for length_ , type_ in cigar_tuples:
        if type_ == "=":
            matches += length_
            aln_length += length_
        else :
            aln_length += length_
    return matches / float(aln_length)



def edlib_alignment(query, target, mode = "HW", task = 'locations', k=-1):
    result = edlib.align(query, target, task=task, mode=mode, k=k)
    if result['editDistance'] == -1:
        return [0,0], -1, 0
    
    if task == 'locations':
        locations = result['locations']
        ref_start, ref_stop = locations[0][0], locations[0][1]
        accuracy = ((ref_stop - ref_start) - result['editDistance'])/ (ref_stop - ref_start)
    elif task == 'path':
        locations = result['locations']
        ref_start, ref_stop = locations[0][0], locations[0][1]
        cigar_string = result["cigar"]
        accuracy = cigar_to_accuracy(cigar_string)
        # print(accuracy, ( (ref_stop - ref_start) - result['editDistance'])/ (ref_stop - ref_start))
        # print(cigar_string, result['editDistance'], locations, accuracy)
        query_alignment, target_alignment, cigar_tuples = cigar_to_seq(cigar_string, query, target[ref_start: ref_stop+1 ])
        # print(cigar_string)
        # print(query_alignment)
        # print(target_alignment)

    return result['locations'], result['editDistance'], accuracy #, query_alignment, target_alignment



def calc_complessed_score(read_alignment, ref_alignment, m, n):
    """
        Raw score: R = aI +  bX - cO -  dG
        lambda=1.37 and K=0.711
        E=mn2**-S
    """
    states = ['I' if n1 == n2 else 'G' if n1 == '-' or n2 == '-' else 'X' for n1, n2 in zip(read_alignment,ref_alignment) ]
    compressed_profile = [ (element, len(list(i))) for element, i in groupby(states)] 
    print(compressed_profile)
    # return evalue


def calc_evalue(read_alignment, ref_alignment, m, n):
    """
        Raw score: R = aI +  bX - cO -  dG
        lambda=1.37 and K=0.711
        E=mn2**-S
    """
    a, b, c, d = 1, -1, 1,  1
    lambda_=1.37
    K=0.711

    states = ['I' if n1 == n2 else 'G' if n1 == '-' or n2 == '-' else 'X' for n1, n2 in zip(read_alignment,ref_alignment) ]
    I = states.count('I')
    X = states.count('X')
    G = states.count('G')
    O =  len([s1 for s1, s2 in zip(states[:-1],states[1:]) if s1 != s2 and s2 == 'G'])
    if states[0] == 'G': # starts with a gap
        O += 1
    raw_score = a*I +  b*X - c*O -  d*G
    if raw_score < 0:
        raw_score = 0
    bit_score = (lambda_*raw_score - math.log(K) )/ math.log(2)
    evalue = m*n*2**(-bit_score)
    # print(read_alignment)
    # print(ref_alignment)
    print(I,X,G,O)
    print(raw_score, bit_score, evalue)
    return evalue

def contains(sub, pri):
    M, N = len(pri), len(sub)
    i, LAST = 0, M-N+1
    while True:
        try:
            found = pri.index(sub[0], i, LAST) # find first elem in sub
        except ValueError:
            return False
        if pri[found:found+N] == sub:
            return True
        else:
            i = found+1

def get_unique_exon_and_flank_locations(solution, parts_to_exons, exon_id_to_choordinates):
    wiggle_overlap = 5
    unique_part_locations = []
    exon_hit_locations = []
    flank_hit_locations = []
    # start_part_offset, part_pos_max = 0, 2**32
    # prev_part = ""
    # approximate_hit_locations = { } # { part_id : (ref_start, ref_stop, read_start, read_stop) }
    segment_exon_hit_locations = { }
    segment_flank_hit_locations = { } 
    choord_to_exon_id = {}

    # these two variables are used to check which exos are considered start and stop exons in the read alignment
    # any such exons will be allowed to align with segments of hits (emulating semi-global alignments)
    first_part_stop = 2**32
    last_part_start = 0
    # print(solution)
    for mem in solution:
        ref_chr_id, ref_start, ref_stop =  mem.exon_part_id.split('^')
        ref_start, ref_stop = int(ref_start), int(ref_stop)

        if len(unique_part_locations) == 0 or (ref_chr_id, ref_start, ref_stop) != unique_part_locations[-1]: # not to add repeated identical parts
            unique_part_locations.append((ref_chr_id, ref_start, ref_stop))

        exon_ids = parts_to_exons[ref_chr_id][(ref_start, ref_stop)]
        if not exon_ids: # is a flank
            flank_hit_locations.append((ref_chr_id, ref_start, ref_stop))  
            if ref_start - wiggle_overlap <= mem.x < mem.y <= ref_stop + wiggle_overlap:
                if (ref_chr_id, ref_start, ref_stop) in segment_flank_hit_locations:
                    segment_flank_hit_locations[(ref_chr_id, ref_start, ref_stop)][1] =  mem.y
                    segment_flank_hit_locations[(ref_chr_id, ref_start, ref_stop)][3] =  mem.d
                else: 
                    segment_flank_hit_locations[(ref_chr_id, ref_start, ref_stop)] = [mem.x, mem.y, mem.c, mem.d]       
        else:
            # get all exons associated with the part and see if they are hit
            if ref_stop <= first_part_stop:
                first_part_stop = ref_stop
            if ref_start >= last_part_start:
                last_part_start = ref_start

            for exon_id in exon_ids:
                # exon overlaps with mem
                e_start, e_stop = exon_id_to_choordinates[exon_id]
                choord_to_exon_id[(ref_chr_id, e_start, e_stop)] = exon_id

                # print(e_start,e_stop,  mem.x, mem.y )
                if e_start - wiggle_overlap <= mem.x < mem.y <= e_stop + wiggle_overlap:
                    exon_hit_locations.append( (ref_chr_id, e_start, e_stop) )

                    if (ref_chr_id, e_start, e_stop) in segment_exon_hit_locations:
                        segment_exon_hit_locations[(ref_chr_id, e_start,e_stop)][1] =  mem.y
                        segment_exon_hit_locations[(ref_chr_id, e_start,e_stop)][3] =  mem.d
                    else: 
                        segment_exon_hit_locations[(ref_chr_id, e_start,e_stop)] = [mem.x, mem.y, mem.c, mem.d]


        # if (ref_chr_id, ref_start, ref_stop) in approximate_hit_locations:
        #     # increase the end coordinates on the same part reference
        #     approximate_hit_locations[(ref_chr_id, ref_start, ref_stop)][1] = mem.y 
        #     approximate_hit_locations[(ref_chr_id, ref_start, ref_stop)][3] = mem.d
        # else:
        #     approximate_hit_locations[(ref_chr_id, ref_start, ref_stop)] = [mem.x, mem.y, mem.c, mem.d]
    
    # remove duplicates added and sort to get unique ones
    exon_hit_locations = list(set(exon_hit_locations))
    exon_hit_locations.sort(key= lambda x: x[1])
    # print(exon_hit_locations)
    # print(segment_exon_hit_locations)
    # print(flank_hit_locations)
    # print(segment_flank_hit_locations)
    # print(approximate_hit_locations)
    return exon_hit_locations, segment_exon_hit_locations, flank_hit_locations, segment_flank_hit_locations, choord_to_exon_id, first_part_stop, last_part_start


def get_unique_exon_and_flank_choordinates(exon_hit_locations, segment_exon_hit_locations, flank_hit_locations, segment_flank_hit_locations, choord_to_exon_id, parts_to_exons, exon_id_to_choordinates, exon_to_gene, gene_to_small_exons):
    # compress unique exons to only do alignment once 
    unique_exon_choordinates = defaultdict(set)
    unique_exon_choordinates_segments = defaultdict(set)
    unique_flank_choordinates = defaultdict(set)
    unique_flank_choordinates_segments = defaultdict(set)

    # sometimes a mem may hit the part sequence outside of the (true) exon simply because there is a 1/4 chance that the next read nucleotide matches the part outside the exon (even if not belonging to the part)
    # This thresholds allows such a wiggle overlap in hitting mems outside of exom boundaries, the chance that the hit is 6nt or larger is a probablitiy of p=1/(4^6) \approx 0.0002
    wiggle_overlap = 5 
    for (ref_chr_id, e_start, e_stop) in exon_hit_locations:
        # exon_ids = parts_to_exons[ref_chr_id][(e_start, e_stop)]
        # print(parts_to_exons)
        exon_id = choord_to_exon_id[(ref_chr_id, e_start, e_stop)]
        unique_exon_choordinates[ (ref_chr_id, e_start, e_stop) ].add(exon_id)
        
        segm_ref_start, segm_ref_stop, segm_read_start, segm_read_stop = segment_exon_hit_locations[(ref_chr_id, e_start, e_stop)]
        unique_exon_choordinates_segments[(ref_chr_id, e_start, e_stop) ] =  (ref_chr_id, segm_ref_start, segm_ref_stop, exon_id)

        # also add all small exons that may be smaller than minimum MEM size
        # unique_genes = set(gene_id for exon_id in exon_ids for gene_id in exon_to_gene[exon_id])
        unique_genes = set(gene_id for gene_id in exon_to_gene[exon_id])
        # print("LOOOOL", exon_ids)

        small_exons = set(small_exon_id for gene_id in unique_genes for small_exon_id in gene_to_small_exons[gene_id]) 
        # print(small_exons)
        for small_exon_id in small_exons:
            e_start, e_stop = exon_id_to_choordinates[small_exon_id]
            if (ref_chr_id,e_start, e_stop) not in unique_exon_choordinates:
                # print("adding small exon,", e_stop - e_start)
                unique_exon_choordinates[ (ref_chr_id, e_start, e_stop) ].add(small_exon_id)

    for (ref_chr_id, ref_start, ref_stop) in flank_hit_locations:
        # print((ref_start, ref_stop), exon_ids)
        # if not exon_ids: # is a flank
        unique_flank_choordinates[ (ref_chr_id, ref_start, ref_stop) ] = set()
        segm_ref_start, segm_ref_stop, segm_read_start, segm_read_stop = segment_flank_hit_locations[(ref_chr_id, ref_start, ref_stop)]
        # case read starts     read:     [ > 0.2*e_len]   ----------------------------...
        # within start exon    exon: --------------------------------
        if (segm_ref_start - ref_start) > 0.05*(ref_stop - ref_start):
            unique_flank_choordinates_segments[(ref_chr_id, ref_start, ref_stop) ] =  (ref_chr_id, segm_ref_start, segm_ref_stop)

        # case read ends       read:  ...----------------------------   [ > 0.2*e_len]   
        # within end exon      exon:                      ---------------------------------
        if (ref_stop - segm_ref_stop ) > 0.05*(ref_stop - ref_start):
            unique_flank_choordinates_segments[(ref_chr_id,  ref_start, ref_stop) ] =  (ref_chr_id, segm_ref_start, segm_ref_stop)
    # print(unique_exon_choordinates)
    # sys.exit()
    return unique_exon_choordinates, unique_exon_choordinates_segments, unique_flank_choordinates, unique_flank_choordinates_segments


def add_exon_to_mam(read_seq, ref_chr_id, exon_seq, e_start, e_stop, exon_id, mam_instance, min_acc):
    if e_stop - e_start >= 5:
        # exon_seq = ref_exon_sequences[ref_chr_id][(e_start, e_stop)]
        # print((e_start, e_stop))
        # print(exon_seq == ref_seq2)
        # assert exon_seq == ref_seq2
        # print(exon_id, e_stop - e_start)
        # align them to the read and get the best approxinate match
        if e_stop - e_start >= 9:
            locations, edit_distance, accuracy = edlib_alignment(exon_seq, read_seq, mode="HW", task = 'path', k = 0.4*min(len(read_seq), len(exon_seq)) ) 
            # print(locations, edit_distance, accuracy)
            # if 'flank' in exon_id:
            # print(exon_seq)
            if edit_distance >= 0:
                # calc_complessed_score(read_alignment, ref_alignment, len(read_seq), len(exon_seq))
                # e_score = calc_evalue(read_alignment, ref_alignment, len(read_seq), len(exon_seq))
                # start, stop = locations[0]
                # if len(locations) > 1:
                #     print("had more", e_stop - e_start, locations)

                for start, stop in locations:
                    min_segment_length = stop - start + 1 #Edlib end location is inclusive
                    # print(accuracy)
                    # print((e_start, e_stop), locations, edit_distance, min_segment_length, accuracy, (min_segment_length - edit_distance)/float(min_segment_length), (stop - start + 1)*accuracy, (stop - start + 1 - edit_distance)* accuracy, (min_segment_length - edit_distance)/float(min_segment_length))
                    # print(exon_seq)
                    score = accuracy*(min_segment_length - edit_distance) # accuracy*min_segment_length
                    if accuracy > min_acc: #(min_segment_length - edit_distance)/float(min_segment_length) > min_acc:
                        # for exon_id in all_exon_ids: break # only need one of the redundant exon_ids
                        # exon_id = all_exon_ids.pop()
                        # covered_regions.append((start,stop, score, exon_id, ref_chr_id))
                        mam_tuple = mam(e_start, e_stop, start, stop, 
                                score, min_segment_length,  exon_id, ref_chr_id) 
                        mam_instance.append(mam_tuple)
                        # print(mam_tuple)
            # else:
            #     if len(read_seq) + len(exon_seq) < 40000:
            #         read_aln, ref_aln, cigar_string, cigar_tuples, alignment_score = help_functions.parasail_local(read_seq, exon_seq)
            #         locations, edit_distance, accuracy = edlib_alignment(exon_seq, read_seq, mode="HW", task = 'path', k = 0.4*min(len(read_seq), len(exon_seq)) )
            #         print('read',read_seq)
            #         print('Rref',exon_seq)
            #         print(locations, edit_distance, accuracy)
            #         # print(read_aln)
            #         # print(ref_aln)
        
        else: # small exons between 5-9bp needs exact match otherwise too much noise
            locations, edit_distance, accuracy = edlib_alignment(exon_seq, read_seq, mode="HW", task = 'path', k = 0 )
            # print("HEEERE", exon_seq, locations, e_start, e_stop,ref_chr_id)
            if edit_distance == 0:
                # print("perfect matches:",exon_seq, locations)
                score = len(exon_seq)
                # calc_complessed_score(read_alignment, ref_alignment, len(read_seq), len(exon_seq))
                # e_score = calc_evalue(read_alignment, ref_alignment, len(read_seq), len(exon_seq))
                # for exon_id in all_exon_ids: break # only need one of the redundant exon_ids
                # exon_id = all_exon_ids.pop()

                for start, stop in locations:
                    # covered_regions.append((start,stop, score, exon_id, ref_chr_id))
                    mam_tuple = mam(e_start, e_stop, start, stop, 
                            score, score,  exon_id, ref_chr_id) 
                    mam_instance.append(mam_tuple)
                    # print(mam_tuple)
    else:
        pass
        # warning_log_file.write("not aligning exons smaller than 5bp: {0}, {1}, {2}, {3}.\n".format(ref_chr_id, e_start, e_stop, ref_exon_sequences[ref_chr_id][(e_start, e_stop)])) # TODO: align these and take all locations

    if  e_stop - e_start >= 0.8*len(read_seq): # read is potentially contained within exon 
        # print()
        # print("aligning read to exon")
        locations, edit_distance, accuracy = edlib_alignment(read_seq, exon_seq, mode="HW", task = 'path', k = 0.4*min(len(read_seq), len(exon_seq)) )
        # print(exon_seq)
        # print((e_start, e_stop), locations, len(exon_seq), len(read_seq), locations,  edit_distance, accuracy)
        # print()
        if edit_distance >= 0:
            # min_segment_length = min( len(exon_seq) ,len(read_seq) )
            # score = min_segment_length - edit_distance #/len(read_seq)
            
            start, stop = locations[0]
            min_segment_length = stop - start + 1 #Edlib end location is inclusive
            score = accuracy*(min_segment_length - edit_distance) #accuracy*min_segment_length  #/len(read_seq)
            # print("LOOK:", min_segment_length, edit_distance, score, locations)
            # if e_score < 1.0:
            if accuracy > min_acc: #(min_segment_length -  edit_distance)/float(min_segment_length) > min_acc:
                start, stop = 0, len(read_seq) - 1
                # covered_regions.append((start,stop, score, exon_id, ref_chr_id))
                # for exon_id in all_exon_ids:
                #     mam_tuple = mam(e_start, e_stop, start, stop, 
                #             score, min_segment_length,  exon_id, ref_chr_id)
                #     mam_instance.append(mam_tuple)
                
                # for exon_id in all_exon_ids: break
                # exon_id = all_exon_ids.pop()
                mam_tuple = mam(e_start, e_stop, start, stop, 
                        score, min_segment_length,  exon_id, ref_chr_id)
                mam_instance.append(mam_tuple)
    


def main(solution, ref_exon_sequences, ref_flank_sequences, parts_to_exons, exon_id_to_choordinates, exon_to_gene, gene_to_small_exons, read_seq, warning_log_file, min_acc):
    """
        NOTE: if paramerer task = 'path' is given to edlib_alignment function calls below, it will give exact accuracy of the aligmnent but the program will be ~40% slower to calling task = 'locations'
            Now we are approxmating accuracy by dividing by start and end of the reference coordinates of the alignment. This is not good approw if there is a large instertion
            in the exon w.r.t. the read.
    """
    # chained_parts_seq = []
    # chained_parts_ids = []
    # prev_ref_stop = -1
    # predicted_transcript = []
    # predicted_exons = []
    # covered_regions = []

    exon_hit_locations, segment_exon_hit_locations, flank_hit_locations, segment_flank_hit_locations, choord_to_exon_id, first_part_stop, last_part_start = get_unique_exon_and_flank_locations(solution, parts_to_exons, exon_id_to_choordinates)
    # print()
    # print(exon_hit_locations)
    # print()

    unique_exon_choordinates, unique_exon_choordinates_segments, \
    unique_flank_choordinates, unique_flank_choordinates_segments = get_unique_exon_and_flank_choordinates(exon_hit_locations, segment_exon_hit_locations, flank_hit_locations, segment_flank_hit_locations, \
                                                                                                     choord_to_exon_id, parts_to_exons, exon_id_to_choordinates, exon_to_gene, gene_to_small_exons)
    # print()
    # print('unique_exon_choordinate segments', unique_exon_choordinates_segments)
    # for t in sorted(unique_exon_choordinates_segments):
    #     print(t)
    # print()
    # sys.exit()

    # unique_exon_segments = get_segments_of_exons(approximate_hit_locations, unique_exon_choordinates)
    # all_potential_hits = unique_exon_choordinates + unique_exon_segments

    # In the chainer solvers, start and end cordinates are always inclusive, i.e. 1,10 means that the mem
    # spans and includes bases 1,2,...,10. In python indexing of strings we would slice out this interval
    # as [1:11], therefore we subtract 1 from the end of the interval before adding it to MAM instance
    mam_instance = []
    for (ref_chr_id, e_start, e_stop), all_exon_ids in sorted(unique_exon_choordinates.items(), key=lambda x: x[0][1]):
        exon_seq = ref_exon_sequences[ref_chr_id][(e_start, e_stop)]
        exon_id = all_exon_ids.pop()
        # print("Testing full exon", e_start, e_stop, exon_id, exon_seq)
        add_exon_to_mam(read_seq, ref_chr_id, exon_seq, e_start, e_stop, exon_id, mam_instance, min_acc)


    # add the flanks if any in the solution But they are required to be start and end flanks of the part MEMs and not overlapping any exons (i.e., the exon hits to be considered)
    for (ref_chr_id, f_start, f_stop), _ in sorted(unique_flank_choordinates.items(), key=lambda x: x[0][1]):
        flank_seq = ref_flank_sequences[ref_chr_id][(f_start, f_stop)]
        flank_id = "flank_{0}_{1}".format(f_start, f_stop)
        # print("adding full flank:", f_start, f_stop, flank_seq )
        # if (f_stop <= exon_hit_locations[0][1]) or (exon_hit_locations[-1][2] <= f_start): # is start flank
        add_exon_to_mam(read_seq, ref_chr_id, flank_seq, f_start, f_stop, flank_id, mam_instance, min_acc)


    # Consider segments here after all full exons and flanks have been aligned. A segment is tested for 
    # all exons/flanks with start/ end coordinate after the choort of the last valid MAM added!

    # Do not allow segments of internal exons yet (ONLY START and END EXON FOR NOW) because these can generate spurious optimal alignments.
    # print(unique_exon_choordinates_segments)
    # print(exon_hit_locations)
    # print("first exon_hit_locations:", first_part_stop, exon_hit_locations[0][2])
    # print("Last exon_hit_locations:", last_part_start, exon_hit_locations[-1][1])
    if len(mam_instance) > 0:
        first_valid_mam_stop = min([m.y for m in mam_instance])
        last_valid_mam_start = max([m.x for m in mam_instance])
    else:
        first_valid_mam_stop = -1
        last_valid_mam_start = 2**32
    final_first_stop = max(first_part_stop, first_valid_mam_stop)
    final_last_start = min(last_part_start, last_valid_mam_start)
    # print(first_part_stop >= first_valid_mam_stop, "OMG")
    # print(last_part_start <= last_valid_mam_start, "OMG2")
    segm_already_tried = set()
    for (ref_chr_id, e_start, e_stop) in unique_exon_choordinates_segments:
        # ref_chr_id, e_start, e_stop, exon_id = unique_exon_choordinates_segments[(ref_chr_id, s_start, s_stop)]
        ref_chr_id, s_start, s_stop, exon_id = unique_exon_choordinates_segments[(ref_chr_id, e_start, e_stop)]
        # is first or last hit exon only
        # print(e_stop, first_valid_mam_stop, first_part_stop, exon_hit_locations[0][2])
        # print(e_start, last_valid_mam_start, last_part_start, exon_hit_locations[-1][1])
        exon_seq = ref_exon_sequences[ref_chr_id][(e_start, e_stop)]        
        if e_stop <= final_first_stop: # is start exon
            segment_seq = exon_seq[s_start - e_start:  ]  # We allow only semi global hit towards one end (the upstream end of the read)
            # print()
            if segment_seq not in segm_already_tried and len(segment_seq) > 5:
                # print("testing segment1:", e_start, e_stop, s_start, s_stop, segment_seq )
                add_exon_to_mam(read_seq, ref_chr_id, segment_seq, e_start, e_stop, exon_id, mam_instance, min_acc)
                segm_already_tried.add(segment_seq)
        elif final_last_start <= e_start: # is end_exon
            # print(len(exon_seq), s_start,s_stop, e_start, e_stop, len(exon_seq), s_start - e_start, len(exon_seq) - (e_stop - s_stop +1))
            # segment_seq = exon_seq[s_start - e_start: len(exon_seq) - (e_stop - (s_stop + 1)) ]  # segment is MEM coordinated i.e. inclusive, so we subtract one here
            segment_seq = exon_seq[: len(exon_seq) - (e_stop - (s_stop + 1)) ]  # segment is MEM coordinated i.e. inclusive, so we subtract one here, allow semi global hit towards one end (the downstream end of the read)
            # print()
            if segment_seq not in segm_already_tried and len(segment_seq) > 5:
                # print("testing segment2:", e_start, e_stop, s_start, s_stop, segment_seq )
                add_exon_to_mam(read_seq, ref_chr_id, segment_seq, e_start, e_stop, exon_id, mam_instance, min_acc)
                segm_already_tried.add(segment_seq)


    # finally add eventual segments of the flanks if any in the solution But they are required not to overlap any exons 
    segm_already_tried = set()
    for (ref_chr_id, f_start, f_stop) in unique_flank_choordinates_segments:
        ref_chr_id, s_start, s_stop = unique_flank_choordinates_segments[(ref_chr_id, f_start, f_stop)]
        flank_seq = ref_flank_sequences[ref_chr_id][(f_start, f_stop)]
        flank_id = "flank_{0}_{1}".format(f_start, f_stop)

        segment_seq = flank_seq[s_start - f_start:  ]   # segment is MEM coordinated i.e. inclusive, so we subtract one here
        if segment_seq not in segm_already_tried and len(segment_seq) > 5:
            # print("Testing start flank segment:", f_start, s_stop, segment_seq )
            add_exon_to_mam(read_seq, ref_chr_id, segment_seq, f_start, f_stop, flank_id, mam_instance, min_acc)
            segm_already_tried.add(segment_seq)

        segment_seq = flank_seq[: len(flank_seq) - (f_stop - (s_stop + 1)) ]  # segment is MEM coordinated i.e. inclusive, so we subtract one here
        if segment_seq not in segm_already_tried and len(segment_seq) > 5:
            # print("Testing end flank segment:", s_start, f_stop, segment_seq )
            add_exon_to_mam(read_seq, ref_chr_id, segment_seq, f_start, f_stop, flank_id, mam_instance, min_acc)
            segm_already_tried.add(segment_seq)


    ###################################################################################################
    ###################################################################################################
    ###################################################################################################
    # print("MAM INSTANCE", mam_instance)
    if mam_instance:
        mam_solution, value, unique = colinear_solver.read_coverage_mam_score(mam_instance)
    else:
        return [], -1, []
    # print(mam_solution)
    covered = sum([mam.d-mam.c + 1 for mam in mam_solution])
    if len(mam_solution) > 0:
        non_covered_regions = []
        non_covered_regions.append( mam_solution[0].c )
        if len(mam_solution) > 1:
            for mam1, mam2 in zip(mam_solution[:-1],mam_solution[1:]):
                non_covered_regions.append( mam2.c - mam1.d -1 )
            # non_covered_regions = [mam2.c-mam1.d for mam1, mam2 in zip(mam_solution[:-1],mam_solution[1:])]
        # add beginning and end
        non_covered_regions.append( len(read_seq)  - mam_solution[-1].d )


    else:
        non_covered_regions = []
    # print(non_covered_regions)
    return non_covered_regions, value, mam_solution


