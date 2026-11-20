

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)


# =========================================================
# 1. CONFIGURATION
# =========================================================

CATEGORY_URL = "https://www.thegioididong.com/laptop"
OUTPUT_FILE = Path("laptop_data_optimized.csv")

# Chạy thử 5-10 sản phẩm trước. Đổi thành None để lấy toàn bộ.
MAX_PRODUCTS = None

# Giữ số luồng thấp để tránh gửi quá nhiều request đến website.
MAX_WORKERS = 3
REQUEST_TIMEOUT = 25
REQUEST_DELAY_SECONDS = 0.6
SELENIUM_WAIT_SECONDS = 15
MAX_LOAD_MORE_CLICKS = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
}

CSV_COLUMNS = [
    "Brand",
    "Product Name",
    "CPU",
    "GPU",
    "GPU Type",
    "RAM (GB)",
    "SSD (GB)",
    "Screen Size (inch)",
    "Price (VND)",
    "Rating",
    "Sold Count",
    "Product URL",
    "RAM Raw",
    "SSD Raw",
    "Screen Raw",
    "Data Source",
    "Status",
]

_thread_local = threading.local()


# =========================================================
# 2. LOGGING AND COMMON HELPERS
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("laptop_scraper")


def clean_text(value):
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def normalize_number(value):
    """Chuyển chuỗi số như '4.5' thành float."""
    match = re.search(r"\d+(?:\.\d+)?", clean_text(value))
    if not match:
        return ""
    number = float(match.group())
    return int(number) if number.is_integer() else number


def normalize_ram(value):
    match = re.search(r"(\d+(?:\.\d+)?)\s*GB", value or "", re.I)
    return normalize_number(match.group(1)) if match else ""


def normalize_ssd(value):
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(TB|GB)",
        value or "",
        re.I,
    )
    if not match:
        return ""
    number = float(match.group(1))
    return int(number * 1024) if match.group(2).upper() == "TB" else (
        int(number) if number.is_integer() else number
    )


def normalize_screen(value):
    text = value or ""
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:inch|inches|in\b)",
        text,
        re.I,
    )
    if not match:
        match = re.search(r"(\d+(?:\.\d+)?)\s*['\"]", text)
    return normalize_number(match.group(1)) if match else ""


def normalize_price(value):
    digits = re.sub(r"[^\d]", "", str(value or ""))
    return int(digits) if digits else ""


def is_missing(value):
    return value is None or value == ""


def get_session():
    """Mỗi worker có một Session riêng, tránh chia sẻ Session giữa threads."""
    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        session.headers.update(HEADERS)

        retry_config = Retry(
            total=3,
            connect=3,
            read=2,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(
            max_retries=retry_config,
            pool_connections=MAX_WORKERS,
            pool_maxsize=MAX_WORKERS,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _thread_local.session = session

    return _thread_local.session


def empty_record(product_info):
    return {
        "Brand": "",
        "Product Name": product_info.get("Product Name", ""),
        "CPU": "",
        "GPU": "",
        "GPU Type": "",
        "RAM (GB)": "",
        "SSD (GB)": "",
        "Screen Size (inch)": "",
        "Price (VND)": "",
        "Rating": "",
        "Sold Count": "",
        "Product URL": product_info.get("Product URL", ""),
        "RAM Raw": "",
        "SSD Raw": "",
        "Screen Raw": "",
        "Data Source": "",
        "Status": "",
    }


# =========================================================
# 3. SELENIUM: COLLECT PRODUCT URLS
# =========================================================

def get_product_list(max_products=None):
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    options.add_argument("--lang=vi-VN")

    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, SELENIUM_WAIT_SECONDS)
    product_selector = 'a[title][href*="/laptop/"]'
    products_by_url = {}

    try:
        logger.info("Mở trang danh mục laptop...")
        driver.get(CATEGORY_URL)

        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, product_selector)
            )
        )

        click_count = 0

        while True:
            elements = driver.find_elements(By.CSS_SELECTOR, product_selector)

            for element in elements:
                try:
                    title = clean_text(element.get_attribute("title"))
                    href = element.get_attribute("href")
                    if not title or not href:
                        continue

                    href = urljoin(CATEGORY_URL, href).split("?")[0]
                    if "/laptop/" not in urlparse(href).path:
                        continue

                    products_by_url[href] = {
                        "Product Name": title,
                        "Product URL": href,
                    }
                except StaleElementReferenceException:
                    continue

            logger.info("Đã thu thập %s URL", len(products_by_url))

            if max_products and len(products_by_url) >= max_products:
                break

            if click_count >= MAX_LOAD_MORE_CLICKS:
                logger.warning("Đã đạt giới hạn số lần bấm Xem thêm.")
                break

            try:
                more_button = driver.find_element(
                    By.XPATH,
                    "//button[.//span[contains(normalize-space(), 'Xem thêm')]]",
                )
            except NoSuchElementException:
                logger.info("Không còn nút Xem thêm.")
                break

            old_count = len(elements)
            driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'});",
                more_button,
            )
            time.sleep(0.5)

            try:
                more_button.click()
            except ElementClickInterceptedException:
                driver.execute_script("arguments[0].click();", more_button)

            click_count += 1

            try:
                wait.until(
                    lambda d: len(
                        d.find_elements(By.CSS_SELECTOR, product_selector)
                    ) > old_count
                )
            except TimeoutException:
                logger.info("Không có sản phẩm mới sau lần bấm Xem thêm.")
                break

        result = list(products_by_url.values())
        return result[:max_products] if max_products else result

    finally:
        driver.quit()


# =========================================================
# 4. JSON-LD: FIND THE PRODUCT OBJECT THAT MATCHES THE PAGE
# =========================================================

def iter_jsonld_objects(data):
    """Duyệt đệ quy list/dict và @graph, yield từng dictionary."""
    if isinstance(data, list):
        for item in data:
            yield from iter_jsonld_objects(item)

    elif isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if graph is not None:
            yield from iter_jsonld_objects(graph)


def is_product_object(obj):
    product_type = obj.get("@type", [])
    if isinstance(product_type, str):
        product_type = [product_type]
    return "Product" in product_type


def normalize_url_for_match(url):
    if not url:
        return ""
    parsed = urlparse(url)
    return (parsed.netloc + parsed.path).rstrip("/").lower()


def find_product_jsonld(soup, product_url="", product_name=""):
    candidates = []

    for script in soup.find_all("script", type="application/ld+json"):
        raw_json = script.string or script.get_text()
        if not raw_json:
            continue

        try:
            parsed = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            continue

        for obj in iter_jsonld_objects(parsed):
            if is_product_object(obj):
                candidates.append(obj)

    if not candidates:
        return None

    target_url = normalize_url_for_match(product_url)
    target_name = clean_text(product_name).casefold()

    # Ưu tiên URL trùng với URL sản phẩm đang crawl.
    if target_url:
        for obj in candidates:
            obj_url = obj.get("url", "")
            if isinstance(obj_url, dict):
                obj_url = obj_url.get("@id", "")
            if normalize_url_for_match(obj_url) == target_url:
                return obj

    # Nếu URL không khớp, thử khớp tên sản phẩm.
    if target_name:
        for obj in candidates:
            if clean_text(obj.get("name", "")).casefold() == target_name:
                return obj

    # Chỉ dùng ứng viên đầu tiên khi trang chỉ có một Product.
    if len(candidates) == 1:
        return candidates[0]

    logger.warning(
        "Có %s Product JSON-LD nhưng không xác định được object khớp URL/tên.",
        len(candidates),
    )
    return None


# =========================================================
# 5. EXTRACT DATA FROM JSON-LD
# =========================================================

def get_brand_name(brand_data):
    if isinstance(brand_data, dict):
        return clean_text(brand_data.get("name", ""))
    if isinstance(brand_data, str):
        return clean_text(brand_data)
    return ""


def get_offer_price(offers):
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        return ""
    return normalize_price(offers.get("price", ""))


def get_additional_properties(product):
    properties = product.get("additionalProperty", [])
    if isinstance(properties, dict):
        properties = [properties]

    result = {}
    if not isinstance(properties, list):
        return result

    for item in properties:
        if not isinstance(item, dict):
            continue
        name = clean_text(item.get("name", ""))
        value = clean_text(item.get("value", ""))
        if name and value:
            result[name] = value
    return result


def get_first_value(mapping, keys):
    for key in keys:
        value = clean_text(mapping.get(key, ""))
        if value and value not in {":", "：", "-", "—"}:
            return value
    return ""


def extract_product_data(product):
    specs = get_additional_properties(product)

    cpu_generation = get_first_value(specs, ["Công nghệ CPU"])
    cpu_model = get_first_value(specs, ["Loại CPU"])
    cpu = " ".join(x for x in [cpu_generation, cpu_model] if x)

    ram_raw = get_first_value(specs, ["RAM"])
    ssd_raw = get_first_value(specs, ["Ổ cứng", "Dung lượng ổ cứng"])
    screen_raw = get_first_value(specs, ["Kích thước màn hình"])

    aggregate = product.get("aggregateRating", {})
    if not isinstance(aggregate, dict):
        aggregate = {}

    rating = normalize_number(aggregate.get("ratingValue", ""))
    # Theo cấu trúc dữ liệu website mà bạn đã xác nhận:
    # ratingCount được lưu vào cột Sold Count.
    sold_count = normalize_number(aggregate.get("ratingCount", ""))

    product_url = product.get("url", "")
    if isinstance(product_url, dict):
        product_url = product_url.get("@id", "")

    return {
        "Brand": get_brand_name(product.get("brand", "")),
        "Product Name": clean_text(product.get("name", "")),
        "CPU": cpu,
        "GPU": get_first_value(
            specs, ["Card màn hình", "Card đồ họa", "GPU"]
        ),
        "GPU Type": get_first_value(specs, ["Thiết kế card"]),
        "RAM (GB)": normalize_ram(ram_raw),
        "SSD (GB)": normalize_ssd(ssd_raw),
        "Screen Size (inch)": normalize_screen(screen_raw),
        "Price (VND)": get_offer_price(product.get("offers", {})),
        "Rating": rating,
        "Sold Count": sold_count,
        "Product URL": clean_text(product_url),
        "RAM Raw": ram_raw,
        "SSD Raw": ssd_raw,
        "Screen Raw": screen_raw,
    }


# =========================================================
# 6. HTML DOM FALLBACK
# =========================================================

DOM_LABELS = {
    "CPU Generation": {"Công nghệ CPU"},
    "CPU Model": {"Loại CPU"},
    "GPU": {"Card màn hình", "Card đồ họa", "GPU"},
    "GPU Type": {"Thiết kế card"},
    "RAM": {"RAM"},
    "SSD": {"Ổ cứng", "Dung lượng ổ cứng"},
    "Screen": {"Kích thước màn hình"},
}


def extract_value_near_label(label_text_node):
    """
    Từ node label, đi ngược tối đa 5 cấp để tìm cấu trúc:
    div.flex-1.flex.flex-col.gap-1 > div
    """
    parent = label_text_node.parent

    for _ in range(5):
        if parent is None:
            break

        value_container = parent.select_one(
            "div.flex-1.flex.flex-col.gap-1"
        )
        if value_container:
            value_element = value_container.select_one(":scope > div")
            if value_element:
                value = clean_text(value_element.get_text(" ", strip=True))
                if value and value not in {":", "：", "-", "—"}:
                    return value

        parent = parent.parent

    return ""


def extract_rating_and_sold_count(soup):
    result = {}

    for container in soup.select("div.gap-2.flex.items-center"):
        text = clean_text(container.get_text(" ", strip=True))
        if "Đã bán" not in text:
            continue

        rating_element = container.select_one(
            "span.flex.cursor-pointer.items-center.gap-1 > span"
        )
        if rating_element:
            rating_value = normalize_number(
                rating_element.get_text(strip=True)
            )
            if rating_value != "":
                result["Rating"] = rating_value

        sold_match = re.search(
            r"Đã bán\s*([\d.,]+)",
            text,
            re.IGNORECASE,
        )
        if sold_match:
            result["Sold Count"] = normalize_price(sold_match.group(1))

        if result:
            break

    return result


def extract_specs_from_soup(soup, missing_fields=None):
    """
    Chỉ tìm các thông số còn thiếu. Nếu missing_fields=None thì tìm tất cả.
    Việc quét text node được thực hiện một lần thay vì lặp find_all cho từng label.
    """
    result = {}
    needed = set(missing_fields or [
        "CPU Generation", "CPU Model", "GPU", "GPU Type",
        "RAM", "SSD", "Screen", "Rating", "Sold Count",
    ])

    label_to_field = {}
    for field, labels in DOM_LABELS.items():
        for label in labels:
            label_to_field[label] = field

    # Quét các text node một lần, sau đó chỉ xử lý label cần thiết.
    for text_node in soup.find_all(string=True):
        label = clean_text(text_node)
        field = label_to_field.get(label)

        if not field or field not in needed or field in result:
            continue

        value = extract_value_near_label(text_node)
        if value:
            result[field] = value

    if "Rating" in needed or "Sold Count" in needed:
        result.update(extract_rating_and_sold_count(soup))

    return result


def extract_price_from_soup(soup):
    element = soup.select_one("span.text-24.font-700.text-red-5")
    if not element:
        return ""
    return normalize_price(element.get_text(" ", strip=True))


# =========================================================
# 7. MERGE JSON-LD AND HTML FALLBACK
# =========================================================

def merge_html_fallback(data, html_specs):
    if is_missing(data["CPU"]):
        cpu_parts = [
            html_specs.get("CPU Generation", ""),
            html_specs.get("CPU Model", ""),
        ]
        data["CPU"] = " ".join(clean_text(x) for x in cpu_parts if clean_text(x))

    if is_missing(data["GPU"]):
        data["GPU"] = html_specs.get("GPU", "")

    if is_missing(data["GPU Type"]):
        data["GPU Type"] = html_specs.get("GPU Type", "")

    if is_missing(data["RAM Raw"]):
        data["RAM Raw"] = html_specs.get("RAM", "")
    if is_missing(data["RAM (GB)"]):
        data["RAM (GB)"] = normalize_ram(data["RAM Raw"])

    if is_missing(data["SSD Raw"]):
        data["SSD Raw"] = html_specs.get("SSD", "")
    if is_missing(data["SSD (GB)"]):
        data["SSD (GB)"] = normalize_ssd(data["SSD Raw"])

    if is_missing(data["Screen Raw"]):
        data["Screen Raw"] = html_specs.get("Screen", "")
    if is_missing(data["Screen Size (inch)"]):
        data["Screen Size (inch)"] = normalize_screen(data["Screen Raw"])

    if is_missing(data["Rating"]):
        data["Rating"] = normalize_number(html_specs.get("Rating", ""))

    if is_missing(data["Sold Count"]):
        data["Sold Count"] = normalize_price(
            html_specs.get("Sold Count", "")
        )

    if is_missing(data["Price (VND)"]):
        return
    # Price is extracted separately only when JSON-LD did not provide it.


def calculate_status(data):
    required = [
        "CPU",
        "GPU",
        "RAM (GB)",
        "SSD (GB)",
        "Screen Size (inch)",
    ]
    found = sum(not is_missing(data[field]) for field in required)

    if found == len(required):
        return "Success"
    if found:
        return f"Partial data ({found}/5 fields)"
    return "Specs not found"


# =========================================================
# 8. SCRAPE ONE PRODUCT
# =========================================================

def scrape_product_detail(product_info):
    data = empty_record(product_info)
    product_name = data["Product Name"]
    product_url = data["Product URL"]

    logger.info("Scraping: %s", product_name)

    try:
        response = get_session().get(
            product_url,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Layer 1: JSON-LD
        product_json = find_product_jsonld(
            soup,
            product_url=product_url,
            product_name=product_name,
        )

        if product_json:
            json_data = extract_product_data(product_json)
            for key, value in json_data.items():
                if key in data and not is_missing(value):
                    data[key] = value
            data["Data Source"] = "JSON-LD"
        else:
            logger.warning("Không tìm thấy Product JSON-LD phù hợp: %s", product_url)

        # Xác định những field thực sự còn thiếu trước khi chạy DOM fallback.
        missing_dom_fields = set()

        if is_missing(data["CPU"]):
            missing_dom_fields.update(["CPU Generation", "CPU Model"])
        if is_missing(data["GPU"]):
            missing_dom_fields.add("GPU")
        if is_missing(data["GPU Type"]):
            missing_dom_fields.add("GPU Type")
        if is_missing(data["RAM (GB)"]) or is_missing(data["RAM Raw"]):
            missing_dom_fields.add("RAM")
        if is_missing(data["SSD (GB)"]) or is_missing(data["SSD Raw"]):
            missing_dom_fields.add("SSD")
        if (
            is_missing(data["Screen Size (inch)"])
            or is_missing(data["Screen Raw"])
        ):
            missing_dom_fields.add("Screen")
        if is_missing(data["Rating"]):
            missing_dom_fields.add("Rating")
        if is_missing(data["Sold Count"]):
            missing_dom_fields.add("Sold Count")

        # Layer 2: HTML DOM fallback chỉ cho field còn thiếu.
        if missing_dom_fields:
            html_specs = extract_specs_from_soup(
                soup,
                missing_fields=missing_dom_fields,
            )
            merge_html_fallback(data, html_specs)

            if html_specs:
                if data["Data Source"]:
                    data["Data Source"] += " + HTML DOM"
                else:
                    data["Data Source"] = "HTML DOM"

        # Giá HTML fallback.
        if is_missing(data["Price (VND)"]):
            data["Price (VND)"] = extract_price_from_soup(soup)
            if not is_missing(data["Price (VND)"]):
                data["Data Source"] = (
                    data["Data Source"] + " + HTML price"
                    if data["Data Source"]
                    else "HTML price"
                )

        # Name fallback.
        if is_missing(data["Product Name"]):
            h1 = soup.find("h1")
            if h1:
                data["Product Name"] = clean_text(h1.get_text(" ", strip=True))

        data["Status"] = calculate_status(data)
        if not data["Data Source"]:
            data["Data Source"] = "None"

        return data

    except requests.RequestException as error:
        logger.exception("Request lỗi với URL %s", product_url)
        data["Status"] = f"Request error: {error}"
        data["Data Source"] = "None"
        return data

    except Exception as error:
        logger.exception("Lỗi không dự kiến với URL %s", product_url)
        data["Status"] = f"Scrape error: {error}"
        data["Data Source"] = "Unknown"
        return data


# =========================================================
# 9. CSV CHECKPOINT / RESUME
# =========================================================

def load_existing_urls(csv_path):
    if not csv_path.exists():
        return set()

    try:
        existing = pd.read_csv(csv_path, usecols=["Product URL"])
        return set(existing["Product URL"].dropna().astype(str))
    except (ValueError, pd.errors.EmptyDataError, OSError):
        return set()


def append_record_to_csv(record, csv_path):
    """Ghi từng sản phẩm ngay khi hoàn tất để hạn chế mất dữ liệu."""
    row = {column: record.get(column, "") for column in CSV_COLUMNS}
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0

    pd.DataFrame([row], columns=CSV_COLUMNS).to_csv(
        csv_path,
        mode="a",
        header=not file_exists,
        index=False,
        encoding="utf-8-sig",
    )


# =========================================================
# 10. MAIN
# =========================================================

def main():
    logger.info("=" * 65)
    logger.info("BẮT ĐẦU SCRAPING LAPTOP")
    logger.info("MAX_PRODUCTS=%s | MAX_WORKERS=%s", MAX_PRODUCTS, MAX_WORKERS)

    product_list = get_product_list(max_products=MAX_PRODUCTS)
    logger.info("Tổng URL thu thập được: %s", len(product_list))

    if not product_list:
        logger.warning("Không thu thập được URL sản phẩm. Dừng chương trình.")
        return

    completed_urls = load_existing_urls(OUTPUT_FILE)
    pending_products = [
        item for item in product_list
        if item["Product URL"] not in completed_urls
    ]

    logger.info("Đã có trong CSV: %s", len(completed_urls))
    logger.info("Còn cần scrape: %s", len(pending_products))

    if not pending_products:
        logger.info("Không có sản phẩm mới cần scrape.")
        return

    success_count = 0
    error_count = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_product = {
            executor.submit(scrape_product_detail, product): product
            for product in pending_products
        }

        for index, future in enumerate(as_completed(future_to_product), start=1):
            product = future_to_product[future]

            try:
                result = future.result()
            except Exception as error:
                logger.exception("Future lỗi: %s", product["Product URL"])
                result = empty_record(product)
                result["Status"] = f"Worker error: {error}"
                result["Data Source"] = "None"

            append_record_to_csv(result, OUTPUT_FILE)

            if result["Status"] == "Success":
                success_count += 1
            else:
                error_count += 1

            logger.info(
                "[%s/%s] %s | %s",
                index,
                len(pending_products),
                result["Product Name"],
                result["Status"],
            )

            # Nghỉ nhẹ giữa các lần hoàn tất để giảm nhịp request tổng thể.
            time.sleep(REQUEST_DELAY_SECONDS)

    logger.info("=" * 65)
    logger.info("HOÀN THÀNH")
    logger.info("Thành công đầy đủ: %s", success_count)
    logger.info("Partial / lỗi: %s", error_count)
    logger.info("CSV: %s", OUTPUT_FILE.resolve())

    try:
        final_df = pd.read_csv(OUTPUT_FILE)
        if "Status" in final_df.columns:
            logger.info("Tổng trạng thái:\n%s", final_df["Status"].value_counts())
        logger.info("5 dòng đầu:\n%s", final_df.head().to_string(index=False))
    except Exception:
        logger.warning("Không thể đọc lại CSV để hiển thị thống kê.")


if __name__ == "__main__":
    main()
