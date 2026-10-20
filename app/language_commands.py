"""Exact, short language-picker commands; these never enter a text field."""
import re


def language_command(text):
    words=re.findall(r'[a-z]+',text.lower())
    if not words or len(words)>8:
        return None
    aliases={'english':'en','inggris':'en','indonesia':'id','indonesian':'id'}
    allowed=set(aliases)|{'both','keduanya','dua','bahasa','and','dan','or','atau','pilih','choose','select','language','languages','the','to','ke','change','ganti'}
    if any(word not in allowed for word in words):
        return None
    chosen={aliases[word] for word in words if word in aliases}
    if {'both','keduanya'} & set(words) or chosen=={'en','id'}:
        return 'both'
    return next(iter(chosen)) if len(chosen)==1 else None


class BilingualBackend:
    """Constrain Whisper's per-segment language token selection to EN and ID."""
    def __init__(self,backend):self.backend=backend
    def __getattr__(self,name):return getattr(self.backend,name)
    def detect_language(self,*args,**kwargs):
        batches=self.backend.detect_language(*args,**kwargs)
        result=[]
        for batch in batches:
            choices=[(token,probability) for token,probability in batch if token in ('<|en|>','<|id|>')]
            total=sum(p for _,p in choices)
            result.append(sorted([(token,p/total if total else .5) for token,p in choices],key=lambda item:item[1],reverse=True)
                          or [('<|en|>',.5),('<|id|>',.5)])
        return result
