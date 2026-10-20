"""Independent-name recall: surnames the tool's name registry does NOT contain
(2000 Census, fewer than 1,000 bearers; international list), first names
from an unrelated list. Frames mimic clinical writing, with and without titles."""
import sys, csv, random
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
SRC = sys.argv[1] if len(sys.argv) > 1 else '.'
from inkguard.policy import evaluate
from inkguard.name_registry import NAMES
random.seed(97)
rare=[r['name'].title() for r in csv.DictReader(open(os.path.join(SRC,'census2000.csv'))) if int(r['count'])<1000 and r['name'].lower() not in NAMES and r['name'].isalpha()]
intl=[l.strip().title() for l in open(os.path.join(SRC,'intl_surnames.txt')) if l.strip().isalpha() and l.strip().lower() not in NAMES]
firsts=[l.strip().title() for l in open(os.path.join(SRC,'intl_first.txt')) if l.strip().isalpha()]
frames=[
 "CC: chest pain. {l} states pain started at rest.",
 "Family hx: mother {f} {l} had breast cancer.",
 "Pt seen at bedside. {l} ambulating in hallway.",
 "Addendum: {l} called back, BP at home 150s.",
 "Received call from {f} {l} regarding lab results.",
 "{l} is POD 2 s/p lap chole, tolerating diet.",
 "Referring physician: {f} {l}",
 "Neuro: {l} alert and oriented x3.",
 "Went over the MRI with {l} and her husband.",
 "Consent obtained from {l} after risks explained.",
]
miss=[]; tot=0; blocked=0
for i in range(1000):
    l=random.choice(rare if i%2==0 else intl); f=random.choice(firsts)
    t=random.choice(frames).format(f=f,l=l,n=random.randint(10000,99999))
    r=evaluate(t)
    if r.action.value=="blocked": blocked+=1; continue
    tot+=1
    import re
    if re.search(r"\b"+re.escape(l)+r"\b", r.clean_text): miss.append((l, r.clean_text[:90]))
print(f"independent surnames: {tot-len(miss)}/{tot} removed, blocked {blocked}")
for m in miss[:40]: print("MISS", m)
