import gradio as gr
import time
import re
from crawler import WebsiteCrawler
from rag_handler import RAGSystem

class WebRAGApp:
    def __init__(self):
        self.crawler = WebsiteCrawler()  
        self.reset_crawler()
        self.rag = None

    def reset_crawler(self):
        self.crawled_pages = None
        self.combined_md = ""
        self.combined_html = ""
        self.per_page_stats = []
        self.summary_stats = []
        self.total_pages = 0
        self.current_page = 0

    def crawl_and_process_markdown(self, url: str, deep_crawl: bool):
        self.reset_crawler()
        if not url.strip() or not (url.startswith("http://") or url.startswith("https://")):
            raise gr.Error("Please enter a valid URL.")
        start_time = time.time()
        crawl_result = self.crawler.crawl_sync(url=url, deep_crawl=deep_crawl)
        if not crawl_result.get("pages"):
            raise gr.Error("No pages found for the given URL.")
        time_taken = time.time() - start_time

        self.crawled_pages = crawl_result["pages"]
        for page in self.crawled_pages:
            self.combined_md += f"# {page['url']}\n\n{page['content']}\n\n"
            self.combined_html += f"<h1>{page['url']}</h1>\n\n{page['html']}\n\n"

        total_images = sum(len(page.get("images", [])) for page in self.crawled_pages)
        total_links = sum(len(page.get("links", [])) for page in self.crawled_pages)
        self.per_page_stats = [
            [page.get("url", "N/A"), len(page.get("images", [])), len(page.get("links", []))]
            for page in self.crawled_pages
        ]
        self.summary_stats = [
            {"Metric": "Total Pages", "Value": crawl_result.get("total_pages", len(self.crawled_pages))},
            {"Metric": "Total Images", "Value": total_images},
            {"Metric": "Total Links", "Value": total_links},
            {"Metric": "Time Taken", "Value": f"{round(time_taken, 2)}s"}
        ]
        formatted_summary = [[item["Metric"], item["Value"]] for item in self.summary_stats]
        self.total_pages = max(1, len(self.per_page_stats) // 4 + (1 if len(self.per_page_stats) % 4 else 0))
        self.structured_md = self.combined_md
        return (crawl_result["pages"], self.combined_md, self.combined_html,
                self.per_page_stats[:4], formatted_summary, f"Page 1 of {self.total_pages}",
                [], [])

    def crawl_and_process_llm(self, url: str, groq_api_key: str):
        self.reset_crawler()
        if not url.strip() or not (url.startswith("http://") or url.startswith("https://")):
            raise gr.Error("Please enter a valid URL.")
        if not groq_api_key.strip():
            raise gr.Error("Groq API key is required for LLM extraction.")
        try:
            start_time = time.time()
            result = self.crawler.crawl_sync(url=url, groq_api_key=groq_api_key)
            time_taken = time.time() - start_time

            extracted = result.get("extracted", [])
            combined_md = ""
            for item in extracted:
                if item.get("error", False):
                    continue
                tag = item.get("tag", "No Tag")
                content_lines = item.get("content", [])
                combined_md += f"# **{tag}**\n" + "\n".join(content_lines) + "\n\n"
            combined_html = result.get("html", "")
            total_extractions = len(extracted)
            total_images = len(result.get("images", []))
            total_links = len(result.get("links", []))
            self.per_page_stats = [["Extraction", total_extractions, "N/A"]]
            self.summary_stats = [
                {"Metric": "Total Extractions", "Value": total_extractions},
                {"Metric": "Total Images", "Value": total_images},
                {"Metric": "Total Links", "Value": total_links},
                {"Metric": "Time Taken", "Value": f"{round(time_taken, 2)}s"}
            ]
            formatted_summary = [[item["Metric"], item["Value"]] for item in self.summary_stats]
            usage_summary_df = [
                ["Completion", str(result.get("usage_summary", {}).get("completion", "N/A"))],
                ["Prompt", str(result.get("usage_summary", {}).get("prompt", "N/A"))],
                ["Total", str(result.get("usage_summary", {}).get("total", "N/A"))]
            ]
            self.crawled_pages = extracted
            self.structured_md = combined_md
            return (extracted, combined_md, combined_html,
                    self.per_page_stats, formatted_summary, "Page 1 of 1",
                    usage_summary_df, [])
        except Exception as e:
            raise gr.Error(f"LLM Crawl failed: {str(e)}")

    def crawl_based_on_method(self, extraction_method: str, url: str, groq_key: str, deep_crawl: bool):
        if extraction_method == "Markdown":
            result = self.crawl_and_process_markdown(url, deep_crawl)
            return (result[0], result[1], result[2],
                    result[3], result[4], result[5],
                    [], [], "")  # Empty LLM data and empty key
        elif extraction_method == "LLM":
            result = self.crawl_and_process_llm(url, groq_key)
            return (result[0], result[1], result[2],
                    [], [], result[5],
                    result[6], result[4], groq_key)  # Pass through API key
        else:
            raise gr.Error("Invalid extraction method selected.")
    
    def prepare_chat(self, groq_api_key: str, model_choice: str):
        if not self.crawled_pages:
            raise gr.Error("Please crawl a website first to load documents.")
        self.rag = RAGSystem(groq_api_key=groq_api_key)
        self.rag.update_model(model_choice)
        
        try:
            # Pass raw markdown directly to RAG system
            self.rag.process_documents(self.structured_md)
            return "Chat system is ready! You can now start interacting with the content."
        except Exception as e:
            raise gr.Error(f"Failed to prepare chat system.")

    def chat_response(self, message: str, history):
        if not self.crawled_pages:
            raise gr.Error("Please crawl a website first to load documents.")
        if not self.rag or not self.rag.vector_store:
            raise gr.Error("Please prepare the chat system first by clicking 'Prepare Chat'.")
        if history is None:
            history = [[None, '**AI:** Welcome! How can I help you today?']]
        # Obtain full response.
        full_response = self.rag.query(message)
        # Apply response filter.
        filtered_response = re.sub(r'<think>.*?</think>\n', '', full_response, flags=re.DOTALL)
        # Update history with the current partial response.
        new_history = history.copy()
        new_history.append([f'**USER:**\n{message}', f"**AI:**{filtered_response}"])
        return new_history

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
        start_idx = self.current_page * 4
        end_idx = start_idx + 4
        current_data = self.per_page_stats[start_idx:end_idx]
        return [current_data, f"Page {self.current_page + 1} of {self.total_pages}"]

    def next_page(self):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
        start_idx = self.current_page * 4
        end_idx = start_idx + 4
        current_data = self.per_page_stats[start_idx:end_idx]
        return [current_data, f"Page {self.current_page + 1} of {self.total_pages}"]

def create_interface():
    app = WebRAGApp()
    
    custom_css = """
    /* Universal scroll container styling */
    .json-scroll-container, .markdown-scroll-container, .html-scroll-container {
        border: 1px solid #ddd;
        border-radius: 8px;
        padding: 10px;
        background: #fff;
        height: 400px !important;
        overflow-y: auto !important;
    }
    /* Remove internal scrollbars */
    .markdown-scroll-container > div:first-child,
    .html-scroll-container > div:first-child {
        height: 100% !important;
        overflow: hidden !important;
    }
    /* Code content styling */
    .markdown-scroll-container textarea,
    .html-scroll-container textarea,
    .json-scroll-container pre {
        height: 100% !important;
        overflow-y: auto !important;
        border: none !important;
        background: transparent !important;
        box-shadow: none !important;
    }
    /* Remove resize handles */
    .markdown-scroll-container textarea,
    .html-scroll-container textarea {
        resize: none !important;
    }
    /* Consistent code formatting */
    .markdown-scroll-container code,
    .html-scroll-container code {
        white-space: pre-wrap !important;
        font-family: monospace !important;
    }
    /* Preserve natural scrolling for other elements */
    body {
        background: #f7f7f7;
        font-family: Arial, sans-serif;
        margin: 0;
        padding: 0;
    }
    .dataframe-table {
        max-height: 300px;
        overflow-y: auto;
        background: #fff;
    }
    .chat-header {
        background: linear-gradient(135deg, #74ABE2, #5563DE);
        color: white;
        padding: 10px;
        text-align: center;
        font-size: 1.25em;
        font-weight: bold;
        border-radius: 4px;
        margin-bottom: 10px;
    }
    .status-message {
        font-style: italic;
        color: green;
        margin-bottom: 10px;
    }
    """
    
    with gr.Blocks(title="Web Knowledge Assistant", theme=gr.themes.Soft(), css=custom_css) as demo:
        gr.Markdown("# 🌐 Web Knowledge Assistant\nCrawl websites and interact with their content seamlessly.")
        with gr.Tab("📥 Crawl Website"):
            with gr.Row():
                url_input = gr.Textbox(label="Website URL", placeholder="https://example.com")
                extraction_method_radio = gr.Radio(choices=["Markdown", "LLM"], label="Extraction Method", value="Markdown", interactive=True)
            with gr.Row():
                groq_api_key_input_crawl = gr.Textbox(label="Groq API Key", placeholder="Enter your Groq API key", type="password", visible=False)
                model_dropdown_crawl = gr.Dropdown(
                    choices=["deepseek-r1-distill-llama-70b", "mixtral-8x7b-32768", "llama3-70b-8192", "llama-3.3-70b-specdec", "llama-3.3-70b-versatile"],
                    value="deepseek-r1-distill-llama-70b", label="Groq Model", visible=False, interactive=True)
            with gr.Row(visible=True) as markdown_crawl_buttons:
                deep_crawl_btn = gr.Button("Start Deep Crawling", variant="secondary")
                base_crawl_btn = gr.Button("Start Base Crawling", variant="primary")
            with gr.Row(visible=False) as llm_crawl_button:
                crawl_btn = gr.Button("Start Crawling", variant="primary")

            extraction_method_radio.change(
                lambda method: (
                    gr.update(visible=(method == "LLM")),  # Groq API key
                    gr.update(visible=(method == "LLM")),   # Model dropdown
                    gr.update(visible=(method == "Markdown")),  # Markdown buttons
                    gr.update(visible=(method == "LLM"))    # LLM button
                ),
                inputs=[extraction_method_radio],
                outputs=[groq_api_key_input_crawl, model_dropdown_crawl, markdown_crawl_buttons, llm_crawl_button]
            )
            with gr.Accordion("Preview Content", open=False):
                with gr.Row(equal_height=True):
                    with gr.Column():
                        gr.Markdown("### JSON Preview")
                        with gr.Column(elem_classes=["json-scroll-container"]):
                            json_preview = gr.JSON(container=False)
                    with gr.Column():
                        gr.Markdown("### Combined Markdown")
                        with gr.Column(elem_classes=["markdown-scroll-container"]):
                            md_preview = gr.Code(language="markdown", container=False, show_label=False)
                    with gr.Column():
                        gr.Markdown("### Combined HTML")
                        with gr.Column(elem_classes=["html-scroll-container"]):
                            html_preview = gr.Code(language="html", show_label=False, container=False)
            with gr.Accordion("Statistics", open=False):
                with gr.Column(visible=True) as markdown_stats_container:
                    with gr.Row():
                        with gr.Column():
                            gr.Markdown("### Page/Extraction Statistics")
                            per_page_df = gr.Dataframe(headers=["Page URL", "Images", "Links"], interactive=False)
                            with gr.Row():
                                prev_btn = gr.Button("← Previous", variant="secondary")
                                page_info = gr.Textbox(label="Page Info", interactive=False)
                                next_btn = gr.Button("Next →", variant="secondary")
                        with gr.Column():
                            gr.Markdown("### Summary Statistics")
                            summary_df = gr.Dataframe(headers=["Metric", "Value"], interactive=False)
                with gr.Column(visible=False) as llm_stats_container:
                    with gr.Row():
                        with gr.Column():
                            gr.Markdown("### Summary Statistics")
                            summary_df_llm = gr.Dataframe(headers=["Metric", "Value"], interactive=False)
                        with gr.Column():
                            gr.Markdown("### Token Usage Summary")
                            usage_summary_df = gr.Dataframe(headers=["Type", "Count"], interactive=False)
            extraction_method_radio.change(
                lambda method: (
                    gr.update(visible=(method == "LLM")),
                    gr.update(visible=(method == "Markdown"))
                ),
                inputs=[extraction_method_radio],
                outputs=[llm_stats_container, markdown_stats_container]
            )
        with gr.Tab("💬 Chat with Content"):
            with gr.Column():
                gr.Markdown("<div class='chat-header'>Chat with Crawled Content</div>")
                with gr.Accordion("Chat Configuration", open=True):
                    with gr.Row():
                        groq_api_key_input_chat = gr.Textbox(
                            label="Groq API Key",
                            placeholder="Enter your Groq API key",
                            type="password",
                            value=""  # Initialize empty
                        )
                        model_dropdown_chat = gr.Dropdown(
                            choices=["deepseek-r1-distill-llama-70b", "mixtral-8x7b-32768", "llama3-70b-8192", "llama-3.3-70b-specdec", "llama-3.3-70b-versatile"],
                            value="deepseek-r1-distill-llama-70b",
                            label="Groq Model",
                            interactive=True
                        )
                        prepare_chat_btn = gr.Button("Prepare Chat", variant="primary")
                    status_msg = gr.Markdown("", elem_classes=["status-message"])
                chatbot = gr.Chatbot(value=[("**AI:** Welcome! How can I help you today?", None)], height=500)
                with gr.Row():
                    msg_input = gr.Textbox(label="Your Query", placeholder="Ask about the website content...", container=False, scale=2)
                    clear_btn = gr.Button("Clear History")
        base_crawl_btn.click(
            fn=app.crawl_based_on_method,
            inputs=[extraction_method_radio, url_input, groq_api_key_input_crawl, gr.State(False)],
            outputs=[
                json_preview, md_preview, html_preview, 
                per_page_df, summary_df, page_info, 
                usage_summary_df, summary_df_llm,
                groq_api_key_input_chat
            ],
        )
        deep_crawl_btn.click(
            fn=app.crawl_based_on_method,
            inputs=[extraction_method_radio, url_input, groq_api_key_input_crawl, gr.State(True)],
            outputs=[
                json_preview, md_preview, html_preview, 
                per_page_df, summary_df, page_info, 
                usage_summary_df, summary_df_llm,
                groq_api_key_input_chat
            ]
        )
        crawl_btn.click(
            fn=app.crawl_based_on_method,
            inputs=[extraction_method_radio, url_input, groq_api_key_input_crawl, gr.State(False)],
            outputs=[
                json_preview, md_preview, html_preview, 
                per_page_df, summary_df, page_info, 
                usage_summary_df, summary_df_llm,
                groq_api_key_input_chat
            ]
        )
        prev_btn.click(fn=app.prev_page, outputs=[per_page_df, page_info])
        next_btn.click(fn=app.next_page, outputs=[per_page_df, page_info])
        prepare_chat_btn.click(
            fn=app.prepare_chat,
            inputs=[groq_api_key_input_chat, model_dropdown_chat],
            outputs=[status_msg]
        )
        msg_input.submit(
            fn=app.chat_response,
            inputs=[msg_input, chatbot],
            outputs=[chatbot],
            queue=False,
            show_progress='minimal',
            scroll_to_output=True
        ).then(lambda: "", None, [msg_input])
        clear_btn.click(lambda: None, None, [chatbot], queue=False)
    return demo

if __name__ == "__main__":
    interface = create_interface()
    try:
        interface.launch(
            server_port=7860, 
            show_error=True, 
            inbrowser=True,
            share=False)
    except Exception as e:
        print(e)