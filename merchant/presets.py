PRESETS = {
    "本月销量与销售额": """SELECT COALESCE(SUM(quantity),0) AS 销量_件,
ROUND(COALESCE(SUM(quantity*unit_price_cents),0)/100.0,2) AS 销售额_元
FROM orders WHERE status IN ('paid','completed')
AND order_date >= '2026-10-01' AND order_date < '2026-10-21'""",
    "本月各商品毛利": """SELECT p.product_name AS 商品,
ROUND(SUM(o.quantity*(o.unit_price_cents-o.unit_cost_cents))/100.0,2) AS 毛利_元
FROM orders o JOIN products p ON o.product_id=p.product_id
WHERE o.status IN ('paid','completed') AND o.order_date >= '2026-10-01' AND o.order_date < '2026-10-21'
GROUP BY p.product_id,p.product_name ORDER BY 毛利_元 DESC""",
    "低库存商品": """SELECT p.product_name AS 商品, i.on_hand-i.reserved AS 可售库存_件,
p.safety_stock AS 安全库存_件 FROM inventory i JOIN products p ON i.product_id=p.product_id
WHERE i.on_hand-i.reserved < p.safety_stock ORDER BY 可售库存_件 ASC""",
}
