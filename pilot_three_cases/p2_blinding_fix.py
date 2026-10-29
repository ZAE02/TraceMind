"""Find and repair reviewer-order exposure; never overwrite original ratings."""
import shutil
import generator as g
from p2_validation import P2, ROOT, read, write

def main():
    affected={}
    for reviewer in ('R1','R2'):
        order=read(P2/'blind_ablation'/reviewer/'order.json')['node_order']
        seen={};risk=[]
        for node in order:
            public=read(ROOT/'checkpoints/public'/(node+'.json'))
            cid=public['case_id'];position=(public['current_session'],int(public['current_user_utterance']['id'].split('-U')[1]))
            if cid in seen and seen[cid]>position:risk.append(node)
            seen[cid]=max(position,seen.get(cid,position))
        risk.sort(key=lambda node:(node.split('_')[0],read(ROOT/'checkpoints/public'/(node+'.json'))['current_session']))
        affected[reviewer]=risk
        out=P2/'blind_order_correction'/reviewer;out.mkdir(parents=True,exist_ok=True)
        write(out/'order.json',{'node_order':risk,'reason':'Earlier reviewer read a later node from same case before this node; fresh-reviewer correction required.'})
        for node in risk:
            write(out/(node+'.json'),read(P2/'blind_ablation'/reviewer/(node+'.json')))
    write(P2/'review_order_risk.json',{'created_at':g.now(),'affected_nodes':affected,
        'scope':'Possible reviewer context contamination only. Answer model requests were stateless and strictly frozen.',
        'correction':'Fresh model reviewers, within-case chronological order; original grades retained separately.'})
    archive=P2/'adjudication/v1_before_order_correction';archive.mkdir(exist_ok=True)
    for name in ('decisions.json','ablation_disagreements.json','order.json'):
        original=P2/'adjudication'/name
        if original.exists() and not (archive/name).exists():shutil.copyfile(str(original),str(archive/name))
    print(affected)

if __name__=='__main__':main()
