-- Dataset Overview

SELECT
    COUNT(*) AS Total_Products,
    COUNT(DISTINCT `Product Name`) AS Unique_Products,
    COUNT(DISTINCT `Brand`) AS Total_Brands,
    ROUND(AVG(`Price (VND)`), 0) AS Average_Price,
    MIN(`Price (VND)`) AS Minimum_Price,
    MAX(`Price (VND)`) AS Maximum_Price
FROM laptop_products;


-- Product Share by Brand

SELECT
    Brand,
    COUNT(*) AS Product_Count,
    ROUND(
        COUNT(*) * 100.0/
        SUM(COUNT(*)) OVER(),2
    ) AS Market_Share
FROM laptop_products
GROUP BY Brand
ORDER BY Product_Count DESC;


-- Median Of Price
WITH Ranked_Prices AS(
    SELECT `Price (VND)`,ROW_NUMBER() over (Order By `Price (VND)`) AS rn,
           COUNT(*) OVER() AS total_rows
    FROM laptop_products
)

SELECT
    ROUND(AVG(`Price (VND)`),0) AS Median_Price
FROM Ranked_Prices
WHERE rn IN(
    FLOOR((total_rows+1)/2),
    FLOOR((total_rows+2)/2)
    );


-- Price Segment

SELECT
    CASE
        WHEN `Price (VND)` < 20000000
            THEN 'Budget'
        WHEN `Price (VND)` < 35000000
            THEN 'Mid-range'
        WHEN `Price (VND)` < 50000000
            THEN 'Upper Mid-range'
        ELSE 'Premium'
    END AS Price_Segment,
    COUNT(*) AS Product_Count

FROM laptop_products
GROUP BY Price_Segment
ORDER BY Product_Count DESC;


-- Statistics Most Popular Ram And SSD Sizes

SELECT
    `RAM (GB)`,
    `SSD (GB)`,
    COUNT(*) AS Product_Count
FROM laptop_products
GROUP BY
    `RAM (GB)`,
    `SSD (GB)`
ORDER BY Product_Count DESC;


-- Statistics of Laptop Product Counts by CPU Vendor And CPU Family

SELECT `CPU Vendor`,`CPU Family`,COUNT(*) AS Product_Count
FROM laptop_products
GROUP BY `CPU Vendor`,`CPU Family`
ORDER BY Product_Count DESC;


-- Are there many laptops on the market that use integrated GPUs or discrete GPUs?

SELECT
    `GPU Type`,
    COUNT(*) AS Product_Count,
    ROUND(
        COUNT(*) * 100.0 /
        SUM(COUNT(*)) OVER(),
        2
    ) AS Product_Share
FROM laptop_products
WHERE `GPU Type` IS NOT NULL
GROUP BY `GPU Type`
ORDER BY Product_Count DESC;


-- Analysis of Dedicated and Integrated GPUs Across Laptop Price Segments

WITH Price_Quartiles AS (
    SELECT
        `GPU Type`,
        `Price (VND)`,
        NTILE(4) OVER (
            ORDER BY `Price (VND)`
        ) AS Price_Quartile
    FROM laptop_products
    WHERE
        `GPU Type` IN ('Dedicated', 'Integrated')
        AND `Price (VND)` IS NOT NULL
),

Quartile_Counts AS (
    SELECT
        `GPU Type`,
        Price_Quartile,
        COUNT(*) AS Product_Count
    FROM Price_Quartiles
    GROUP BY
        `GPU Type`,
        Price_Quartile
)

SELECT
    `GPU Type`,
    Price_Quartile,
    CASE
        WHEN Price_Quartile = 1 THEN 'Q1 - Lowest Price'
        WHEN Price_Quartile = 2 THEN 'Q2 - Low-Mid Price'
        WHEN Price_Quartile = 3 THEN 'Q3 - Mid-High Price'
        WHEN Price_Quartile = 4 THEN 'Q4 - Highest Price'
    END AS Price_Segment,
    Product_Count,
    ROUND(
        Product_Count * 100.0 /
        SUM(Product_Count) OVER (
            PARTITION BY `GPU Type`
        ),
        2
    ) AS Percentage
FROM Quartile_Counts
ORDER BY
    `GPU Type`,
    Price_Quartile;

-- Compare pricing Strategies between laptop Brands.

SELECT
    Brand,
    COUNT(*) AS Product_Count,
    ROUND(AVG(`Price (VND)`), 0) AS Average_Price,
    ROUND(MIN(`Price (VND)`), 0) AS Min_Price,
    ROUND(MAX(`Price (VND)`), 0) AS Max_Price
FROM laptop_products
GROUP BY Brand
HAVING COUNT(*) >= 5
ORDER BY Average_Price DESC;



-- Sales Performance by Brand

SELECT
    Brand,
    COUNT(*) AS Product_Count,
    SUM(`Sold Count`) AS Total_Sold

FROM laptop_products
WHERE `Sold Count` IS NOT NULL
GROUP BY Brand
ORDER BY Total_Sold DESC;



-- Sales Performance by Price Segment

SELECT
    CASE
        WHEN `Price (VND)` < 20000000 THEN 'Budget'
        WHEN `Price (VND)` < 35000000 THEN 'Mid-range'
        WHEN `Price (VND)` < 50000000 THEN 'Upper Mid-range'
        ELSE 'Premium'
    END AS Price_Segment,
    SUM(`Sold Count`) AS Total_Sold

FROM laptop_products
WHERE `Sold Count` IS NOT NULL
GROUP BY Price_Segment
ORDER BY Total_Sold DESC;

