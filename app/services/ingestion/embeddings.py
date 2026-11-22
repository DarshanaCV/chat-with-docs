 # Create embeddings
import logging
from app.logging_setup import progress
from transformers import AutoTokenizer, AutoModel
import torch

model_name="sentence-transformers/all-MiniLM-L6-v2"

with progress(logging.getLogger(__name__), "Loading the text embedding model"):
    tokenizer=AutoTokenizer.from_pretrained(model_name)
    model=AutoModel.from_pretrained(model_name)

def create_embeddings(texts):
    # text->token
    inputs=tokenizer(texts,padding=True,truncation=True,return_tensors="pt")
    with torch.no_grad():
        outputs=model(**inputs)
    token_embeddings=outputs.last_hidden_state
    attention_mask=inputs['attention_mask'] #padding
    mask=attention_mask.unsqueeze(-1) #adds dimension to match the dimension of token_embeddings
    masked_embeddings=token_embeddings*mask #aplly the attenstion mask
    sum_embeddings=masked_embeddings.sum(dim=1) # sum of all token embeddings in a sentence
    sum_mask=mask.sum(dim=1) # sum of real tokens in a sentence
    embeddings=sum_embeddings/sum_mask # average of all real token embeddings in a sentence
    embeddings=torch.nn.functional.normalize(embeddings,p=2,dim=1) # normalize to hve sum 1
    return embeddings.numpy()
