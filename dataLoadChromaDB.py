import pandas as pd
import ChromaDB

# Support parameter
CHUNK_SIZE = 1000  # Number of rows to process in each chunk
OVERLAP_SIZE = 100  # Number of overlapping rows between chunks
SUPPORTED_FILE_TYPES = ['pdf', 'txt']  # Supported file types for loading data

# Create chunking function
def chunk_dataframe(df,chunk_size=CHUNK_SIZE, overlap_size=OVERLAP_SIZE):
    """
    Splits a DataFrame into chunks with specified size and overlap.

    Parameters:
        df (pd.DataFrame): The DataFrame to be chunked.
        chunk_size (int): The number of rows in each chunk.
        overlap_size (int): The number of overlapping rows between chunks.

    Returns:
        List[pd.DataFrame]: A list of DataFrames, each representing a chunk.
    """
    chunks = []
    start = 0
    while start < len(df):
        end = min(start + chunk_size, len(df))
        chunk = df.iloc[start:end]
        chunks.append(chunk)
        start += chunk_size - overlap_size  # Move the start index for the next chunk
    return chunks
def initialize_chromadb(db):
    """
    Initializes the ChromaDB instance.

    Parameters:
        db (ChromaDB): An instance of ChromaDB to be initialized.
    """
    # Assuming ChromaDB has an initialize method
    db.initialize()  # Replace with actual initialization code

   
# Tokenize and load file name and content into ChromaDB
def load_file_to_chromadb(content, file_name):
    """
    Loads the content of a file into ChromaDB after tokenizing it.

    Parameters:
        content (str): The content to be loaded.
        file_name (str): The name of the file.
    """
    # Assuming a function to initialize ChromaDB exists
    # Tokenize the content (assuming a function tokenize exists)
   

    # Create a DataFrame from tokens
    df = pd.DataFrame({'content': [content]})  # Assuming content is a string; adjust as needed

    # Chunk the DataFrame
    chunks = chunk_dataframe(df)
    # Tokenize each chunk and store in ChromaDB
    tokanized_chunks = [tokenize(chunk) for chunk in chunks]  # Assuming a tokenize function exists
    client=ChromaDB.ParsistantClient(db_path="db/chromadb")  # Replace with actual path
    collection= client.get_or_create_collection("doc_keyword_search")  # Replace with actual collection name
    # collection upsert source and metadata/text embedding
    collection.upsert(
        documents=[chunk.to_dict(orient='records') for chunk in chunks],
        metadatas=[{"source": file_name} for _ in chunks],  # Replace with actual source info
        ids=[f"chunk_{i}" for i in range(len(chunks))]  # Unique IDs for each chunk
    )
    
   

