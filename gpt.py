import torch
import torch.nn as nn
from torch.nn import functional as F

NUM_LAYERS = 6
NUM_HEADS = 6
SEQ_LEN = 256 # maximum context length for one input
EMBED_SIZE = 384 # embedding dimension size
DROPOUT = 0.2
DEVICE = 'mps' if torch.backends.mps.is_available() else 'cpu'

class FeedForward(nn.Module):
    def __init__(self, n_embd):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(n_embd, 4 * n_embd),
            nn.ReLU(),
            nn.Linear(4 * n_embd, n_embd), # projection layer
            nn.Dropout(DROPOUT),
        )

    def forward(self, x):
        return self.net(x)

class Head(nn.Module):
    '''Single head of self-attention.'''
    def __init__(self, head_size):
        super().__init__()
        self.head_size = head_size
        self.key = nn.Linear(EMBED_SIZE, head_size, bias=False)
        self.query = nn.Linear(EMBED_SIZE, head_size, bias=False)
        self.value = nn.Linear(EMBED_SIZE, head_size, bias=False)
        self.register_buffer('tril', torch.tril(torch.ones(SEQ_LEN,SEQ_LEN)))
        self.dropout = nn.Dropout(DROPOUT)

    def forward(self, x):
        B,T,C = x.shape
        k = self.key(x) # (B, T, head_size)
        q = self.query(x) # (B, T, head_size)
        # compute attention scores / affinities
        wei = q @ k.transpose(-2, -1) / k.shape[-1] ** 0.5 # (B,T,head_size) * (B,head_size,T) = (B,T,T)  
        wei = wei.masked_fill(self.tril[:T, :T] == 0, float('-inf')) # (B,T,T)
        wei = F.softmax(wei, dim=-1) # (B,T,T)
        wei = self.dropout(wei)
        # perform weighted aggregation of values
        v = self.value(x) # (B, T, head_size)
        out = wei @ v  # (B, T, T) @ (B, T, head_size) = (B, T, head_size)
        return out

class MultiHeadAttention(nn.Module):
    def __init__(self, num_heads, head_size):
        super().__init__()
        self.heads = nn.ModuleList([Head(head_size) for _ in range(num_heads)])
        self.proj = nn.Linear(num_heads * head_size, EMBED_SIZE) # project output to dimensions allowing for residual (x = x + layer(x))
        self.dropout = nn.Dropout(DROPOUT)
    
    def forward(self, x):
        out = torch.cat([h(x) for h in self.heads], dim=-1)
        out = self.proj(out)
        out = self.dropout(out)
        return out

class Block(nn.Module):
    '''Transformer block'''
    def __init__(self, num_embd, num_heads):
        super().__init__()
        # head size = embedding dim / num heads
        head_size = num_embd // num_heads
        self.sa = MultiHeadAttention(num_heads, head_size)
        self.ffwd = FeedForward(num_embd)
        self.ln1 = nn.LayerNorm(num_embd)
        self.ln2 = nn.LayerNorm(num_embd)

    def forward(self, x):
        x = x + self.sa(self.ln1(x))
        x = x + self.ffwd(self.ln2(x))
        return x

class GPT(nn.Module):
  def __init__(self, vocab_size):
    super().__init__()
    self.token_embedding_table = nn.Embedding(vocab_size, EMBED_SIZE)
    self.position_embedding_table = nn.Embedding(SEQ_LEN, EMBED_SIZE)
    self.blocks = nn.Sequential(*[Block(EMBED_SIZE, NUM_HEADS) for _ in range(NUM_LAYERS)])
    self.ln_f = nn.LayerNorm(EMBED_SIZE) # final layer norm
    self.lm_head = nn.Linear(EMBED_SIZE, vocab_size)
    self.apply(self._init_weights)

  def _init_weights(self, module):
    if isinstance(module, nn.Linear):
        torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)
        if module.bias is not None:
            torch.nn.init.zeros_(module.bias)
    elif isinstance(module, nn.Embedding):
        torch.nn.init.normal_(module.weight, mean=0.0, std=0.02)
        
  def forward(self, idx, targets=None):
    B, T = idx.shape

    # idx and targets are both (B,T) tensor of integers
    tok_emb = self.token_embedding_table(idx) # (B,T,C)
    pos_emb = self.position_embedding_table(torch.arange(T, device=DEVICE)) # (T,C)
    x = tok_emb + pos_emb # (B,T,C)
    x = self.blocks(x) # (B,T,C)
    x = self.ln_f(x) # (B,T,C)
    logits = self.lm_head(x) # (B,T,vocab_size)

    if targets is None:
        loss = None
    else:
        B, T, C = logits.shape
        logits = logits.view(B*T, C)
        targets = targets.view(B*T)
        loss = F.cross_entropy(logits, targets)

    return logits, loss

  def generate(self, idx, max_new_tokens):
    # idx is array of shape (B,T) indices representing the current context
    for _ in range(max_new_tokens):
      # crop context to last block_size tokens
      idx_cond = idx[:, -SEQ_LEN:]
      # get prediction
      logits, loss = self(idx_cond)
      # focus on only the last time step
      logits = logits[:, -1, :] # becomes (B,C) which is the prob of each 65 char for each batch for next time step
      # apply softmax to get probabilities
      probs = F.softmax(logits, dim=1) # dim=1 -> sum of each row probabilities = 1, where 1 row contains probs of each 65 char in vocabulary
      # sample from that probability distribution
      next_idx = torch.multinomial(probs, num_samples=1) # (B,1)
      # add the new index to the context for the next iteration
      idx = torch.cat((idx, next_idx), dim=1) # (B,T+1)

    return idx
