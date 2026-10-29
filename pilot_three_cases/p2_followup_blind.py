"""Blind newly selected-node answers; each selected node belongs to a different case."""
import random
import generator as g
from p2_validation import P2, read, write
from p2_methods import selected

def main():
    key={};by_node={}
    for node,public in selected().items():
        items=[]
        for condition in ('H0_utterance','H1_session','H2_full'):
            for sample in (1,2):
                path=P2/'reselection/answers'/('%s_%s_%d.json'%(node,condition,sample))
                row=read(path);id_='X'+g.digest({'node':node,'condition':condition,'sample':sample})[:12]
                key[id_]={'node':node,'condition':condition,'sample':sample,'call_id':row['call_id'],
                          'source':str(path.relative_to(P2))}
                items.append({'anonymous_id':id_,'answer':row['parsed_answer'],'raw_text':row['raw_text'],
                              'visible_evidence_ids':row['visible_evidence_ids'],
                              'format_valid':isinstance(row['parsed_answer'],dict)})
        by_node[node]={'node':node,'public_input':public,'answers':items}
    write(P2/'internal/reselected_blind_key.json',key)
    for reviewer,seed in (('R1',13731),('R2',23824)):
        rng=random.Random(seed);nodes=sorted(by_node);rng.shuffle(nodes)
        out=P2/'blind_reselected'/reviewer
        write(out/'order.json',{'node_order':nodes,'notice':'One node per case prevents cross-node future context exposure. Read public first and record node judgment before answers.'})
        for node in nodes:
            packet=dict(by_node[node]);packet['answers']=list(packet['answers']);rng.shuffle(packet['answers'])
            write(out/(node+'.json'),packet)
    print('Blinded 18 replacement-node answers; distinct-case nodes only.')

if __name__=='__main__':main()
