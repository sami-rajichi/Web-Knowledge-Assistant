from langchain_core.runnables import RunnablePassthrough
from langchain.text_splitter import MarkdownTextSplitter
from langchain.text_splitter import MarkdownHeaderTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq
from langchain_community.embeddings.sentence_transformer import SentenceTransformerEmbeddings
from langchain_core.output_parsers import StrOutputParser
import gradio as gr

class RAGSystem:
    def __init__(self, groq_api_key: str):
        # Initialize headers to split on (customize based on your needs)
        self.header_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=[("#", "Header 1"), ("##", "Header 2"), ("###", "Header 3")
            ]
        )
        # Use a free, publicly available embedding model.
        self.embedding_model = SentenceTransformerEmbeddings(model_name="all-MiniLM-L6-v2")
        self.text_splitter = MarkdownTextSplitter(chunk_size=1500, chunk_overlap=300)
        self.vector_store = None
        self._groq_api_key = groq_api_key  # Stored in memory only for security.
        self.model = ChatGroq(
            temperature=0.7,
            model_name="deepseek-r1-distill-llama-70b",
            groq_api_key=groq_api_key
        )

    def process_documents(self, markdown_content: str):  # Changed to accept raw markdown
        if not markdown_content.strip():
            raise gr.Error("No markdown content provided")

        # Split markdown by headers
        splits = self.header_splitter.split_text(markdown_content)
        
        if not splits:
            raise gr.Error("No splits created from markdown content")
            
        try:
            # Create vector store from splits
            self.vector_store = FAISS.from_documents(
                documents=splits,
                embedding=self.embedding_model
            )
        except Exception as e:
            raise gr.Error("Failed to create vector store.")

    def update_api_key(self, groq_api_key: str):
        if not groq_api_key:
            raise gr.Error("Groq API key is required to update the LLM.")
        self._groq_api_key = groq_api_key
        self.model = ChatGroq(
            temperature=0.7,
            model_name=self.model.model_name,
            groq_api_key=groq_api_key,
        )

    def update_model(self, model_name: str):
        if not self._groq_api_key:
            raise gr.Error("Please set the Groq API key first.")
        self.model = ChatGroq(
            temperature=0.7,
            model_name=model_name,
            groq_api_key=self._groq_api_key,
        )

    def query(self, query: str) -> str:
        if not self.vector_store:
            return "Please load documents first."
        if not self.model:
            return "Groq API key is not set. Please update it in the Chat tab."
        # Build the chain using a prompt template.
        template = """Answer the question based only on the following context:
{context}

Question: {question}
"""
        prompt = ChatPromptTemplate.from_template(template)
        chain = (
            {"context": self.vector_store.as_retriever(), "question": RunnablePassthrough()}
            | prompt
            | self.model
            | StrOutputParser()
        )
        return chain.invoke(query)
