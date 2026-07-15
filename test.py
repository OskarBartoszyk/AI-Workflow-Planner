import random
import time
from workflow import Workflow, Task, Run

def job(name, t):
    def run():
        print(f"START {name}")
        time.sleep(t)
        print(f"END   {name}")
    return run


etl = Workflow(name="ETL Pipeline")

fetch_users = etl.add_task(Task("Fetch Users", group="Extract", function=job("Fetch Users", 2)))
fetch_orders = etl.add_task(Task("Fetch Orders", group="Extract", function=job("Fetch Orders", 3)))
fetch_products = etl.add_task(Task("Fetch Products", group="Extract", function=job("Fetch Products", 1)))

clean_users = etl.add_task(Task("Clean Users", group="Transform", function=job("Clean Users", 2)))
clean_orders = etl.add_task(Task("Clean Orders", group="Transform", function=job("Clean Orders", 2)))
aggregate = etl.add_task(Task("Aggregate", group="Transform", function=job("Aggregate", 2)))

warehouse = etl.add_task(Task("Warehouse", group="Load", function=job("Warehouse", 2)))
dashboard = etl.add_task(Task("Dashboard", group="Reports", function=job("Dashboard", 1)))

etl.add_edge(fetch_users, clean_users, directed=True)
etl.add_edge(fetch_orders, clean_orders, directed=True)
etl.add_edge(fetch_products, aggregate, directed=True)

etl.add_edge(clean_users, aggregate, directed=True)
etl.add_edge(clean_orders, aggregate, directed=True)

etl.add_edge(aggregate, warehouse, directed=True)
etl.add_edge(warehouse, dashboard, directed=True)

ml = Workflow(name="ML Training")

download = ml.add_task(Task("Download Dataset", group="Data", function=job("Download", 2)))

eda = ml.add_task(Task("EDA", group="Analysis", function=job("EDA", 2)))
features = ml.add_task(Task("Feature Engineering", group="Analysis", function=job("Features", 3)))
split = ml.add_task(Task("Train/Test Split", group="Analysis", function=job("Split", 1)))

rf = ml.add_task(Task("Random Forest", group="Training", function=job("RF", 5)))
xgb = ml.add_task(Task("XGBoost", group="Training", function=job("XGB", 4)))
nn = ml.add_task(Task("Neural Network", group="Training", function=job("NN", 6)))

compare = ml.add_task(Task("Compare Models", group="Evaluation", function=job("Compare", 2)))
deploy = ml.add_task(Task("Deploy Model", group="Deployment", function=job("Deploy", 2)))

for t in (eda, features, split):
    ml.add_edge(download, t, directed=True)

for t in (rf, xgb, nn):
    ml.add_edge(features, t, directed=True)
    ml.add_edge(split, t, directed=True)

ml.add_edge(rf, compare, directed=True)
ml.add_edge(xgb, compare, directed=True)
ml.add_edge(nn, compare, directed=True)

ml.add_edge(compare, deploy, directed=True)


shop = Workflow(name="E-Commerce Order")

order = shop.add_task(Task("Receive Order", group="Order", function=job("Receive",1)))

payment = shop.add_task(Task("Payment", group="Payment", function=job("Payment",2)))
fraud = shop.add_task(Task("Fraud Check", group="Payment", function=job("Fraud",2)))

inventory = shop.add_task(Task("Reserve Stock", group="Warehouse", function=job("Reserve",2)))
packing = shop.add_task(Task("Pack", group="Warehouse", function=job("Pack",3)))

label = shop.add_task(Task("Shipping Label", group="Shipping", function=job("Label",1)))
courier = shop.add_task(Task("Courier Pickup", group="Shipping", function=job("Courier",2)))

email = shop.add_task(Task("Confirmation Email", group="Notifications", function=job("Email",1)))
invoice = shop.add_task(Task("Invoice", group="Notifications", function=job("Invoice",1)))

shop.add_edge(order, payment, directed=True)
shop.add_edge(order, fraud, directed=True)

shop.add_edge(payment, inventory, directed=True)
shop.add_edge(fraud, inventory, directed=True)

shop.add_edge(inventory, packing, directed=True)
shop.add_edge(packing, label, directed=True)
shop.add_edge(label, courier, directed=True)

shop.add_edge(payment, email, directed=True)
shop.add_edge(payment, invoice, directed=True)


etl.visualize(open_browser=False)
ml.visualize(open_browser=False)
shop.visualize(open_browser=True)

import threading

threading.Thread(target=lambda: Run(etl).execute(), daemon=False).start()
threading.Thread(target=lambda: Run(ml).execute(), daemon=False).start()
threading.Thread(target=lambda: Run(shop).execute(), daemon=False).start()