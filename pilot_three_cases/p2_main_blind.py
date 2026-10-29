"""Publish anonymous method packets only after all eight answers for a node exist."""
import random
import generator as g
from p2_validation import P2,read,write
from p2_methods import selected

METHODS=('B1_full','B2_temporal_RAG','B3_generic_rollup','B4_psych_rollup')

def main():
    key={};packets={};pending=[]
    for node,public in selected().items():
        paths=[(method,sample,P2/'method_answers'/('%s_%s_%d.json'%(node,method,sample)))
               for method in METHODS for sample in (1,2)]
        if any(not path.exists() for _,_,path in paths):pending.append(node);continue
        items=[]
        for method,sample,path in paths:
            row=read(path);id_='Y'+g.digest({'node':node,'method':method,'sample':sample})[:12]
            key[id_]={'node':node,'method':method,'sample':sample,'call_id':row['call_id'],
                      'source':str(path.relative_to(P2))}
            items.append({'anonymous_id':id_,'answer':row['parsed_answer'],'raw_text':row['raw_text'],
                          'visible_evidence_ids':row['visible_evidence_ids'],
                          'format_valid':isinstance(row['parsed_answer'],dict)})
        packets[node]={'node':node,'public_input':public,'answers':items}
    write(P2/'internal/main_blind_key.json',key)
    for reviewer,seed,order in (('R1',9414,['N01','N06','N04']),('R2',1734,['N06','N01','N04'])):
        out=P2/'blind_main'/reviewer;rng=random.Random(seed)
        write(out/'order.json',{'node_order':order,'expected_answers':24,
            'note':'One point per case; within-node answer order independently shuffled. Wait for a missing node file rather than reading other data.'})
        for node,packet in packets.items():
            shuffled=dict(packet);shuffled['answers']=list(packet['answers']);rng.shuffle(shuffled['answers'])
            write(out/(node+'.json'),shuffled)
    write(P2/'blind_main/status.json',{'ready_nodes':list(packets),'pending_nodes':pending,'anonymous_answers':len(key)})
    print({'ready':list(packets),'pending':pending,'answers':len(key)})

if __name__=='__main__':main()
