"""Exact, short language-picker commands; these never enter a text field."""
import re
from contextlib import contextmanager


@contextmanager
def shared_encoder(model):
    """Reuse exact encoder features within one serial language-command request."""
    original=getattr(model,'encode',None)
    if not callable(original):
        yield
        return
    import numpy as np
    missing=object();previous=vars(model).get('encode',missing)
    entries=[]
    def encode(features):
        for source,encoded in entries:
            if source.shape==features.shape and np.array_equal(source,features):return encoded
        encoded=original(features)
        if len(entries)<2:entries.append((features.copy(),encoded))
        return encoded
    model.encode=encode
    try:
        yield
    finally:
        if previous is missing:del model.encode
        else:model.encode=previous
        entries.clear()


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
        return None
    return next(iter(chosen)) if len(chosen)==1 else None
