#!/usr/bin/env python
# coding: utf-8
import os
import torch
import nest_asyncio
from llama_index.core import SimpleDirectoryReader, VectorStoreIndex, StorageContext
from llama_index.core.node_parser import TokenTextSplitter, MarkdownNodeParser
from llama_index.vector_stores.opensearch import OpensearchVectorStore, OpensearchVectorClient
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.core.vector_stores.types import VectorStoreQueryMode
import pickle
import time
import random

# Apply nest_asyncio to avoid runtime errors
nest_asyncio.apply()

# Check if CUDA is available for GPU acceleration
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Using device: {device}")

# Setup OpenSearch
endpoint = os.getenv("OPENSEARCH_ENDPOINT", "http://localhost:9200")
idx = os.getenv("OPENSEARCH_INDEX", "dg_md_index")  # Changed to markdown-specific index
text_field = "content"
embedding_field = "embedding"

# Load documents from md_corpus
reader = SimpleDirectoryReader(
    input_dir="md_corpus", 
    recursive=True,
    required_exts=[".md", ".markdown"],  # Ensure only markdown files are loaded
    file_extractor={}  # Use default extractor which works well with markdown
)
documents = reader.load_data()
print(f"Loaded {len(documents)} markdown documents")

# Create specialized parser for markdown
md_parser = MarkdownNodeParser(chunk_size=1024)
nodes = md_parser.get_nodes_from_documents(documents, show_progress=True)

print(f"Created {len(nodes)} nodes")

# Setup embedding model
embedding_model_name = 'BAAI/bge-m3'
embed_model = HuggingFaceEmbedding(model_name=embedding_model_name, max_length=1024, device=device)

# Get embedding dimension
embeddings = embed_model.get_text_embedding("box")
dim = len(embeddings)
print(f"Embedding dimension: {dim}")

# Retry function for OpenSearch connection
def connect_with_retry(max_retries=5, initial_backoff=1, max_backoff=60):
    retries = 0
    while retries < max_retries:
        try:
            # Setup OpensearchVectorClient with a supported engine
            client = OpensearchVectorClient(
                endpoint=endpoint,
                index=idx,
                dim=dim,
                embedding_field=embedding_field,
                text_field=text_field,
                engine="faiss",  # Use faiss instead of nmslib
                # Remove search_pipeline until we confirm it's configured
                # search_pipeline="hybrid-search-pipeline",
            )
            return client
        except Exception as e:
            retries += 1
            print(f"Error: {str(e)}")
            if retries == max_retries:
                raise e
            
            # Calculate backoff with jitter
            backoff = min(initial_backoff * (2 ** (retries - 1)), max_backoff)
            jitter = random.uniform(0, 0.1 * backoff)
            sleep_time = backoff + jitter
            
            print(f"Connection failed. Retrying in {sleep_time:.2f} seconds... (Attempt {retries}/{max_retries})")
            time.sleep(sleep_time)

# Connect to OpenSearch with retry
client = connect_with_retry()

# Initialize vector store
vector_store = OpensearchVectorStore(client)

# Create storage context
storage_context = StorageContext.from_defaults(vector_store=vector_store)

# Create index
index = VectorStoreIndex(nodes, storage_context=storage_context, embed_model=embed_model)

# Save index
with open('md_index.pkl', 'wb') as f:
    pickle.dump(index, f)
print("Index created and saved to md_index.pkl")

# Test with default retrieval (without hybrid search for now)
retriever = index.as_retriever(similarity_top_k=3)
text_retrieve = "ถูกเข้าถึงระบบคอมโดยไม่ได้รับอนุญาต ผิดกฎหมายฉบับใด ควรสอบสวนอย่างไร"
prompt = retriever.retrieve(text_retrieve)
print("\nTest retrieval results:")
for r in prompt:
    print(f"Metadata: {r.metadata}")
    print(f"Content: {r.text}\n")
    
    # Print section information if available
    section = None
    if "header_text" in r.metadata:
        section = r.metadata["header_text"]
    elif "headers" in r.metadata and r.metadata["headers"]:
        section = r.metadata["headers"][-1]
    
    if section:
        print(f"Section: {section}\n")
