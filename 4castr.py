import asyncio
import aiohttp
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter import font as tkfont
import logging
from datetime import datetime, timedelta, timezone
import os
from dotenv import load_dotenv
from collections import defaultdict
from dateutil import parser
from aiohttp_retry import RetryClient, ExponentialRetry
import math  # Import the math module

# Load .env file
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.DEBUG)

# Square API credentials and endpoints
square_access_token = os.getenv('SQUARE_ACCESS_TOKEN')
location_id = os.getenv('LOCATION_ID')
headers = {
    "Square-Version": "2024-07-17",
    "Authorization": f"Bearer {square_access_token}",
    "Content-Type": "application/json"
}

# GUI setup
root = tk.Tk()
root.title("4castr")

# Variables to store selected categories
selected_categories = []
show_all_var = tk.BooleanVar(value=False)


def get_last_three_months():
    today = datetime.now(timezone.utc)
    first_day_this_month = today.replace(day=1)
    last_month_end = first_day_this_month - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    second_last_month_end = last_month_start - timedelta(days=1)
    second_last_month_start = second_last_month_end.replace(day=1)
    third_last_month_end = second_last_month_start - timedelta(days=1)
    third_last_month_start = third_last_month_end.replace(day=1)

    getmonths = [
        third_last_month_start.strftime('%B %Y'),
        second_last_month_start.strftime('%B %Y'),
        last_month_start.strftime('%B %Y')
    ]

    return getmonths


months = get_last_three_months()
columns = ["Category Name", "Item Name", "Order Needed", "In Stock", f"{months[0]} Sales", f"{months[1]} Sales",
           f"{months[2]} Sales", "3 Months Total Sales", "Avg Daily Sold", "Alert"]
tree = ttk.Treeview(root, columns=columns, show="headings")
for col in columns:
    tree.heading(col, text=col)

tree.pack(fill=tk.BOTH, expand=True)


def adjust_column_width():
    total_width = 0
    font = tkfont.Font()
    for getcol in tree["columns"]:
        max_width = font.measure(getcol) + 10  # Width for the header with padding
        for item in tree.get_children():
            cell_text = str(tree.item(item, 'values')[columns.index(getcol)])
            cell_width = font.measure(cell_text) + 10  # Width for each cell with padding
            if cell_width > max_width:
                max_width = cell_width
        tree.column(getcol, width=max_width)
        total_width += max_width
    total_height = 600  # You can adjust the height as needed
    root.geometry(f"{total_width}x{total_height}")  # Adjust window size to fit columns and a fixed height


def display_forecast():
    asyncio.run(fetch_and_display_forecast())


async def fetch_and_display_forecast():
    try:
        (variation_names, inventory_counts, sales_summary, daily_sales, category_names, forecasted_orders,
         monthly_sales, monthly_maximums) = await fetch_and_forecast()
        tree.delete(*tree.get_children())
        for index, (item_id, name_data) in enumerate(variation_names.items()):
            category_id = name_data.get('category_id') or name_data.get('reporting_category_id')
            category_name = category_names.get(category_id, "Unknown Category")
            total_sold = sum(sales_summary.get(item_id, {}).values())  # Extract total sold
            avg_daily_sold = daily_sales.get(item_id, 0)
            order_needed = forecasted_orders.get(item_id, 0)
            max_monthly_sales = monthly_maximums.get(item_id, 0)  # Get Monthly Max
            if show_all_var.get() or order_needed > 0:
                month_1_sales = monthly_sales[item_id][0]
                month_2_sales = monthly_sales[item_id][1]
                month_3_sales = monthly_sales[item_id][2]
                weekly_sales = avg_daily_sold * 12
                monthly_max_div_10 = max_monthly_sales / 10
                alert_value = math.ceil(weekly_sales + monthly_max_div_10)  # Round up the value
                tree.insert("", tk.END, values=(
                    category_name,
                    name_data['name'],
                    order_needed,
                    inventory_counts.get(item_id, 0),
                    month_1_sales,
                    month_2_sales,
                    month_3_sales,
                    total_sold,
                    f"{avg_daily_sold:.2f}",
                    alert_value), tags=('row',))
        # Configure alternating row colors and font color
        tree.tag_configure('even', background='black', foreground='limegreen')
        tree.tag_configure('odd', background='gray9', foreground='forestgreen')
        for i, item in enumerate(tree.get_children()):
            if i % 2 == 0:
                tree.item(item, tags=('even',))
            else:
                tree.item(item, tags=('odd',))
        adjust_column_width()
    except Exception as e:
        logging.error(f"Failed to fetch and display data: {e}")
        messagebox.showerror("Error", "Failed to fetch and display forecast data.")


async def fetch_and_forecast():
    items = await fetch_items()
    if not items:
        logging.error("No items found in specified categories.")
        return {}, {}, {}, {}, {}, {}, {}, {}

    category_names = await fetch_category_names()
    start_date, end_date, month_dates = calculate_date_range()
    inventory_counts = await fetch_inventory_counts(items)
    sales_summary = await fetch_sales_data(start_date, end_date)

    daily_sales = calculate_daily_sales(sales_summary, parser.isoparse(start_date), parser.isoparse(end_date))
    monthly_maximums = calculate_monthly_maximum(sales_summary, month_dates)
    forecasted_orders = forecast_inventory(monthly_maximums, inventory_counts)
    monthly_sales = calculate_monthly_sales(sales_summary, month_dates)

    return (items, inventory_counts, sales_summary, daily_sales, category_names, forecasted_orders, monthly_sales,
            monthly_maximums)


async def fetch_items():
    async with aiohttp.ClientSession() as session:
        return await fetch_items_with_variants(session)


async def fetch_items_with_variants(session):
    items = {}
    retry_options = ExponentialRetry(attempts=3)
    async with RetryClient(session, retry_options=retry_options) as retry_client:
        for category_id in selected_categories:
            payload = {
                "object_types": ["ITEM"],
                "query": {
                    "exact_query": {
                        "attribute_name": "category_id",
                        "attribute_value": category_id
                    }
                },
                "limit": 100
            }
            try:
                async with retry_client.post('https://connect.squareup.com/v2/catalog/search', headers=headers,
                                             json=payload) as response:
                    data = await response.json()
                    if response.status != 200:
                        logging.error(f"Failed to fetch items: {response.status} {data}")
                        continue

                    for obj in data.get('objects', []):
                        if obj['type'] == 'ITEM':
                            item_name = obj['item_data']['name']
                            category_id = obj['item_data'].get('category_id')
                            reporting_category_id = obj['item_data'].get('reporting_category', {}).get('id')
                            variations = obj['item_data'].get('variations', [])
                            for variation in variations:
                                variation_id = variation['id']
                                variation_name = variation['item_variation_data']['name']
                                items[variation_id] = {
                                    'name': f"{item_name} ({variation_name})",
                                    'category_id': category_id,
                                    'reporting_category_id': reporting_category_id
                                }
            except Exception as e:
                logging.error(f"Exception occurred while fetching items for category {category_id}: {e}")
    return items


async def fetch_category_names():
    async with aiohttp.ClientSession() as session:
        return await fetch_category_names_with_session(session)


async def fetch_category_names_with_session(session):
    category_names = {}
    payload = {
        "object_types": ["CATEGORY"]
    }
    try:
        async with session.post('https://connect.squareup.com/v2/catalog/search', headers=headers,
                                json=payload) as response:
            data = await response.json()
            if response.status != 200:
                logging.error(f"Failed to fetch category names: {response.status} {data}")
                return category_names

            for obj in data.get('objects', []):
                if obj['type'] == 'CATEGORY':
                    category_names[obj['id']] = obj['category_data']['name']
    except Exception as e:
        logging.error(f"Exception occurred while fetching category names: {e}")

    return category_names


def calculate_date_range():
    today = datetime.now(timezone.utc)

    # Get the first day of the current month
    first_day_this_month = today.replace(day=1)

    # Get the first day of the last month
    last_month_end = first_day_this_month - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    # Get the first day of the month before last
    second_last_month_end = last_month_start - timedelta(days=1)
    second_last_month_start = second_last_month_end.replace(day=1)

    # Get the first day of the month three months ago
    third_last_month_end = second_last_month_start - timedelta(days=1)
    third_last_month_start = third_last_month_end.replace(day=1)

    # The start date is the first day of the month three months ago
    start_date = third_last_month_start
    # The end date is the last day of the last month
    end_date = last_month_end

    # Get the dates for each month
    month_dates = [
        (third_last_month_start, second_last_month_start - timedelta(days=1)),
        (second_last_month_start, last_month_start - timedelta(days=1)),
        (last_month_start, last_month_end)
    ]

    return start_date.isoformat(), end_date.isoformat(), month_dates


async def fetch_inventory_counts(items):
    async with aiohttp.ClientSession() as session:
        return await fetch_inventory_counts_with_session(session, items)


async def fetch_inventory_counts_with_session(session, items):
    counts = {}
    item_ids = list(items.keys())
    retry_options = ExponentialRetry(attempts=3)
    async with RetryClient(session, retry_options=retry_options) as retry_client:
        for i in range(0, len(item_ids), 100):
            chunk = item_ids[i:i + 100]
            payload = {
                "catalog_object_ids": chunk,
                "location_ids": [location_id],
                "states": ["IN_STOCK"]
            }
            try:
                async with retry_client.post('https://connect.squareup.com/v2/inventory/counts/batch-retrieve',
                                             headers=headers, json=payload) as response:
                    data = await response.json()
                    if response.status != 200:
                        logging.error(f"Failed to fetch inventory counts: {response.status} {data}")
                        continue

                    for count in data.get('counts', []):
                        if count['state'] == 'IN_STOCK':
                            counts[count['catalog_object_id']] = int(count['quantity'])
            except Exception as e:
                logging.error(f"Exception occurred while fetching inventory counts: {e}")

    return counts


async def fetch_sales_data(start_date, end_date):
    async with aiohttp.ClientSession() as session:
        return await fetch_sales_data_with_session(session, start_date, end_date)


async def fetch_sales_data_with_session(session, start_date, end_date):
    sales = defaultdict(dict)
    cursor = None
    retry_options = ExponentialRetry(attempts=3)
    async with RetryClient(session, retry_options=retry_options) as retry_client:
        while True:
            payload = {
                "location_ids": [location_id],
                "query": {
                    "filter": {
                        "date_time_filter": {
                            "created_at": {
                                "start_at": start_date,
                                "end_at": end_date
                            }
                        },
                        "state_filter": {
                            "states": ["COMPLETED"]
                        }
                    }
                }
            }
            if cursor:
                payload["cursor"] = cursor
            try:
                async with retry_client.post('https://connect.squareup.com/v2/orders/search', headers=headers,
                                             json=payload) as response:
                    data = await response.json()
                    if response.status != 200:
                        logging.error(f"Failed to fetch sales data: {response.status} {data}")
                        return sales

                    for order in data.get('orders', []):
                        order_date = parser.isoparse(order['created_at'])
                        for line_item in order.get('line_items', []):
                            item_id = line_item.get('catalog_object_id')
                            quantity = int(line_item.get('quantity'))
                            sales[item_id][order_date] = sales[item_id].get(order_date, 0) + quantity

                    cursor = data.get('cursor')
                    if not cursor:
                        break
            except Exception as e:
                logging.error(f"Exception occurred while fetching sales data: {e}")
                break

    return sales


def calculate_daily_sales(sales_data, start_date, end_date):
    daily_sales = {}
    num_days = (end_date - start_date).days + 1
    for item_id, sales_by_date in sales_data.items():
        total_sales = sum(sales_by_date.values())
        daily_sales[item_id] = total_sales / num_days
    return daily_sales


def calculate_monthly_maximum(sales_data, month_dates):
    monthly_maximums = {}
    for item_id, sales_by_date in sales_data.items():
        monthly_sales = []
        for start_date, end_date in month_dates:
            # Calculate total sales for the current month
            total_monthly_sales = sum(
                qty for sale_date, qty in sales_by_date.items()
                if start_date <= sale_date <= end_date
            )
            monthly_sales.append(total_monthly_sales)
        # Find the maximum sales among the three months
        max_sales = max(monthly_sales)
        monthly_maximums[item_id] = max_sales

    return monthly_maximums


def calculate_monthly_sales(sales_data, month_dates):
    monthly_sales = defaultdict(lambda: [0, 0, 0])
    for item_id, sales_by_date in sales_data.items():
        for i, (start_date, end_date) in enumerate(month_dates):
            monthly_sales[item_id][i] = sum(
                qty for sale_date, qty in sales_by_date.items()
                if start_date <= sale_date <= end_date
            )
    return monthly_sales


def forecast_inventory(monthly_maximums, current_inventory, buffer_percentage=0.15):
    forecasted_orders = {}
    for item_id, monthly_max in monthly_maximums.items():
        projected_inventory = current_inventory.get(item_id, 0)
        buffer_amount = monthly_max * buffer_percentage
        order_needed = max(0, (monthly_max + buffer_amount) - projected_inventory)
        forecasted_orders[item_id] = int(order_needed)
    return forecasted_orders


def show_category_selector(categories):
    selector_window = tk.Toplevel(root)
    selector_window.title("Select Categories")

    canvas = tk.Canvas(selector_window)
    scrollbar = tk.Scrollbar(selector_window, orient="vertical", command=canvas.yview)
    scrollable_frame = ttk.Frame(canvas)

    scrollable_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")
        )
    )

    canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    category_vars = {category_id: tk.BooleanVar() for category_id in categories}

    # Sort categories alphabetically by name
    sorted_categories = sorted(categories.items(), key=lambda item: item[1])

    for category_id, category_name in sorted_categories:
        tk.Checkbutton(scrollable_frame, text=category_name, variable=category_vars[category_id]).pack(anchor='w')

    def apply_selection():
        global selected_categories
        selected_categories = [category_id for category_id, var in category_vars.items() if var.get()]
        selector_window.destroy()

    apply_button = tk.Button(selector_window, text="Apply", command=apply_selection)
    apply_button.pack()

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")


def open_category_selector():
    asyncio.run(fetch_and_show_categories())


async def fetch_and_show_categories():
    categories = await fetch_category_names()
    show_category_selector(categories)


# Add checkbox to toggle showing all results
show_all_checkbox = tk.Checkbutton(root, text="Show All", variable=show_all_var)
show_all_checkbox.pack()

# Add button to open category selector
category_button = tk.Button(root, text="Select Categories", command=open_category_selector)
category_button.pack()

# Add button to fetch and forecast data
fetch_button = tk.Button(root, text="Fetch and Forecast", command=display_forecast)
fetch_button.pack()

# Run the GUI
root.mainloop()
