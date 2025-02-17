import asyncio
import json
from typing import List, Optional
import aiohttp
import xml.etree.ElementTree as ET
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.extraction_strategy import LLMExtractionStrategy
import gradio as gr

class WebsiteCrawler:
    def __init__(self):
        pass
    
    def setup_crawler(self, groq_api_key: str = None):
        """
        If a groq_api_key is provided, the crawler will use the LLM extraction strategy.
        Otherwise it will perform a standard sitemap crawl.
        """
        # For LLM Extractor
        if groq_api_key is not None:
            self.llm_strat = LLMExtractionStrategy(
                provider="groq/deepseek-r1-distill-llama-70b",
                api_token=groq_api_key,
                overlap_rate=0.2,
                instruction="Extract all the content in a clear and precise manner suitable for RAG chatbots.",
                extra_args={"temperature": 0.7, "max_tokens": 6800, "top_p": 1}
            )
            self.crawl_config = CrawlerRunConfig(
                extraction_strategy=self.llm_strat,
                word_count_threshold=1,
                process_iframes=True,
                remove_overlay_elements=True,
                cache_mode=CacheMode.BYPASS,
                scan_full_page=True,
                delay_before_return_html=5,
                wait_for_images=True,
                scroll_delay=5,
                ignore_body_visibility=False,
                simulate_user=True,
                override_navigator=True,
                adjust_viewport_to_content=True,
            )
        # For Markdown Extractor
        else:
            self.run_config = CrawlerRunConfig(
                word_count_threshold=1,
                exclude_external_links=True,
                process_iframes=True,
                remove_overlay_elements=True,
                cache_mode=CacheMode.BYPASS,
                scan_full_page=True,
                delay_before_return_html=5,
                wait_for_images=True,
                scroll_delay=5,
                ignore_body_visibility=False,
                simulate_user=True,
                override_navigator=True,
                adjust_viewport_to_content=True,
            )
        # Browser Configuration
        self.browser_config = BrowserConfig(
            headless=True,
            verbose=False,
        )

    async def _parse_sitemap(self, base_url: str) -> List[str]:
        """
        Improved sitemap parser with support for multiple formats.
        This method is used only in the plain (non-LLM) crawling mode.
        """
        sitemap_urls = [
            f"{base_url.rstrip('/')}/sitemap.xml",
            f"{base_url.rstrip('/')}/sitemap_index.xml",
            f"{base_url.rstrip('/')}/sitemap.txt"
        ]
        
        async with aiohttp.ClientSession() as session:
            for sitemap_url in sitemap_urls:
                try:
                    async with session.get(sitemap_url, timeout=10) as response:
                        if response.status == 200:
                            content = await response.text()
                            if "sitemapindex" in content.lower():
                                root = ET.fromstring(content)
                                return [loc.text for loc in root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
                            elif "txt" in response.headers.get('Content-Type', ''):
                                return content.splitlines()
                            else:
                                root = ET.fromstring(content)
                                return [loc.text for loc in root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
                except (aiohttp.ClientError, ET.ParseError):
                    continue
        
        return [base_url]

    async def discover_links(self, start_url: str) -> List[str]:
        """Crawls the website to discover internal links and generate a sitemap"""
        visited = set()
        discovered_urls = set()
        results = []
        to_crawl = [start_url]

        async with AsyncWebCrawler(config=self.browser_config) as crawler:
            while to_crawl and len(discovered_urls) < 100:  # Safety limit
                current_url = to_crawl.pop(0)
                if current_url in visited:
                    continue
                
                try:
                    gr.Info(f'Crawling: {current_url}')
                    result = await crawler.arun(url=current_url, config=self.run_config)
                    if result.success:
                        discovered_urls.add(current_url)
                        visited.add(current_url)
                        
                        # Add new internal links to crawl queue
                        new_links = [
                            link['href'] for link in result.links.get("internal", [])
                            if link['href'] not in visited and link['href'] not in to_crawl
                        ]
                        to_crawl.extend(new_links)
                        
                        # Add the whole current page content
                        results.append({
                            "url": result.url,
                            "content": result.markdown,
                            "html": result.html,
                            "images": result.media["images"],
                            "links": result.links["internal"]
                        }) 
                except Exception as e:
                    gr.Warning(f"Error discovering links at {current_url}")
        
        return list(discovered_urls), results

    async def crawl(self, url: str, groq_api_key: str = None, deep_crawl: bool = False) -> dict:
        """
        Asynchronously crawl the given URL.
        In plain mode, the method will parse the sitemap, crawl each URL, and return the results.
        In LLM mode, it will perform a single-page crawl using the LLM extraction strategy.
        """
        self.setup_crawler(groq_api_key)
        if groq_api_key is not None:
            return await self._crawl_with_llm(url)
        else:
            return await self._crawl_plain(url, deep_crawl)

    async def _crawl_plain(self, url: str, deep_crawl: bool) -> dict:
        urls = [url]  # Default to base URL
        results = []
        sitemap_source = "base"
        
        if deep_crawl:
            # First try standard sitemap parsing
            sitemap_urls = await self._parse_sitemap(url)
            
            # If no sitemap found, generate our own
            if len(sitemap_urls) == 1 and sitemap_urls[0] == url:
                gr.Info("No sitemap found, crawling through deep LINK DISCOVERY...")
                discovered_urls, results = await self.discover_links(url)
                urls = discovered_urls if discovered_urls else [url]
                sitemap_source = "generated" if discovered_urls else "fallback"
            else:
                gr.Info("SITEMAP detected, prioritizing structured crawl...")
                urls = sitemap_urls
                sitemap_source = "sitemap"
        else:
            gr.Info("Resorting to BASE crawling...")
            sitemap_source = "base"

        if sitemap_source in ("base", "sitemap"):      
            tasks = []  
            async with AsyncWebCrawler(config=self.browser_config) as crawler:
                for i, u in enumerate(urls):
                    if sitemap_source == 'sitemap':
                        gr.Info(f'URL: {i+1}/{len(urls)}')
                    tasks.append(crawler.arun(url=u, config=self.run_config))
                for future in asyncio.as_completed(tasks):
                    try:
                        result = await future
                        if result.success:
                            results.append({
                                "url": result.url,
                                "content": result.markdown,
                                "html": result.html,
                                "images": result.media["images"],
                                "links": result.links["internal"]
                            })
                    except Exception as e:
                        print(f"Error crawling {result.url}: {str(e)}")

        return {
            "base_url": url,
            "pages": results,
            "total_pages": len(results),
            "sitemap_source": sitemap_source
        }

    async def _crawl_with_llm(self, url: str) -> dict:
        async with AsyncWebCrawler(config=self.browser_config) as crawler:
            result = await crawler.arun(url=url, config=self.crawl_config)
        if result.success:
            try:
                data = json.loads(result.extracted_content)
            except Exception as e:
                raise Exception("Failed to parse extracted content as JSON: " + str(e))
            return {
                "extracted": data,
                "html": result.html,
                "images": result.media["images"],
                "links": result.links["internal"],
                "usage_summary": {
                    "completion": self.llm_strat.total_usage.completion_tokens,
                    "prompt": self.llm_strat.total_usage.prompt_tokens,
                    "total": self.llm_strat.total_usage.total_tokens,
                },
                "usage_history": [
                    {
                        "request": i,
                        "completion": usage.completion_tokens,
                        "prompt": usage.prompt_tokens,
                        "total": usage.total_tokens
                    }
                    for i, usage in enumerate(self.llm_strat.usages, 1)
                ]
            }
        else:
            err = result.error_message if result else "No result returned."
            raise Exception(f"Crawl failed: {err}")

    def crawl_sync(self, url: str, groq_api_key: str = None, deep_crawl: bool = False) -> dict:
        """
        Synchronous wrapper around the asynchronous crawl() method.
        """
        self.groq_api_key = groq_api_key
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self.crawl(url, groq_api_key, deep_crawl))
        finally:
            loop.close()

# c = WebsiteCrawler()
# data = c.crawl_sync(url='https://gewinner.tn/', deep_crawl=True)
# import json
# with open('result.json', 'w') as fp:
#     json.dump(data, fp)