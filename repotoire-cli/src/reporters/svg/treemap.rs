//! Squarified treemap SVG generator for hotspot visualization.

use std::fmt::Write;

pub struct TreemapItem {
    pub label: String,
    pub size: f64,
    pub color_value: f64, // 0.0 (green/healthy) to 1.0 (red/unhealthy)
}

/// Render a squarified treemap as inline SVG.
/// Items are laid out to minimize aspect ratios (squarified algorithm, Bruls et al. 2000).
// write! to a String is infallible, so unwrap() will never panic here.
#[allow(clippy::unwrap_used)]
pub fn render_treemap(items: &[TreemapItem], width: f64, height: f64) -> String {
    let mut svg = String::with_capacity(1024);
    write!(
        svg,
        r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">"#,
    )
    .expect("write! to String is infallible");

    if !items.is_empty() {
        // Sort by size descending and normalize
        let total_size: f64 = items.iter().map(|i| i.size).sum();
        if total_size > 0.0 {
            let total_area = width * height;
            let mut sorted: Vec<(usize, f64)> = items
                .iter()
                .enumerate()
                .map(|(i, item)| (i, item.size / total_size * total_area))
                .collect();
            sorted.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));

            let rects = squarify(&sorted, 0.0, 0.0, width, height);
            for (idx, x, y, w, h) in &rects {
                let item = &items[*idx];
                let color = super::health_color(item.color_value);
                let label = super::xml_escape(&item.label);
                write!(
                    svg,
                    r#"<rect x="{x:.1}" y="{y:.1}" width="{w:.1}" height="{h:.1}" fill="{color}" stroke="white" stroke-width="1" rx="2"/>"#,
                )
                .expect("write! to String is infallible");
                if *w > 60.0 && *h > 20.0 {
                    let tx = x + w / 2.0;
                    let ty = y + h / 2.0 + 4.0;
                    write!(
                        svg,
                        r#"<text x="{tx:.1}" y="{ty:.1}" font-size="11" fill="white" font-family="sans-serif" text-anchor="middle">{label}</text>"#,
                    )
                    .expect("write! to String is infallible");
                }
            }
        }
    }

    svg.push_str("</svg>");
    svg
}

/// Returns Vec of (item_index, x, y, w, h).
fn squarify(
    items: &[(usize, f64)],
    x: f64,
    y: f64,
    w: f64,
    h: f64,
) -> Vec<(usize, f64, f64, f64, f64)> {
    if items.is_empty() {
        return vec![];
    }
    if items.len() == 1 {
        return vec![(items[0].0, x, y, w, h)];
    }

    let mut results = Vec::new();
    let mut row: Vec<(usize, f64)> = Vec::new();
    let mut i = 0;

    while i < items.len() {
        let short_side = w.min(h);
        row.clear();
        row.push(items[i]);
        let mut best_ratio = worst_ratio(&row, short_side);
        i += 1;

        while i < items.len() {
            row.push(items[i]);
            let new_ratio = worst_ratio(&row, short_side);
            if new_ratio > best_ratio {
                // Adding this item made it worse; remove it and finalize row
                row.pop();
                break;
            }
            best_ratio = new_ratio;
            i += 1;
        }

        // Lay out the row
        let row_area: f64 = row.iter().map(|(_, a)| a).sum();
        let (rects, nx, ny, nw, nh) = layout_row(&row, x, y, w, h, row_area);
        results.extend(rects);

        // Recurse on remaining items
        if i < items.len() {
            results.extend(squarify(&items[i..], nx, ny, nw, nh));
            break;
        }
    }

    results
}

fn worst_ratio(row: &[(usize, f64)], side: f64) -> f64 {
    let sum: f64 = row.iter().map(|(_, a)| a).sum();
    if sum <= 0.0 || side <= 0.0 {
        return f64::MAX;
    }
    let s2 = sum * sum;
    let mut worst = 0.0_f64;
    for (_, area) in row {
        if *area <= 0.0 {
            continue;
        }
        let r1 = (side * side * area) / s2;
        let r2 = s2 / (side * side * area);
        let ratio = r1.max(r2);
        worst = worst.max(ratio);
    }
    worst
}

/// Lay out a row of items. Returns (rects, remaining_x, remaining_y, remaining_w, remaining_h).
fn layout_row(
    row: &[(usize, f64)],
    x: f64,
    y: f64,
    w: f64,
    h: f64,
    row_area: f64,
) -> (Vec<(usize, f64, f64, f64, f64)>, f64, f64, f64, f64) {
    let mut rects = Vec::with_capacity(row.len());

    if w >= h {
        // Lay out vertically along the left side
        let row_w = if h > 0.0 { row_area / h } else { 0.0 };
        let mut cy = y;
        for (idx, area) in row {
            let rect_h = if row_w > 0.0 { area / row_w } else { 0.0 };
            rects.push((*idx, x, cy, row_w, rect_h));
            cy += rect_h;
        }
        (rects, x + row_w, y, w - row_w, h)
    } else {
        // Lay out horizontally along the top
        let row_h = if w > 0.0 { row_area / w } else { 0.0 };
        let mut cx = x;
        for (idx, area) in row {
            let rect_w = if row_h > 0.0 { area / row_h } else { 0.0 };
            rects.push((*idx, cx, y, rect_w, row_h));
            cx += rect_w;
        }
        (rects, x, y + row_h, w, h - row_h)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_treemap_renders_svg() {
        let items = vec![
            TreemapItem {
                label: "src/main.rs".into(),
                size: 500.0,
                color_value: 0.2,
            },
            TreemapItem {
                label: "src/lib.rs".into(),
                size: 300.0,
                color_value: 0.8,
            },
            TreemapItem {
                label: "src/utils.rs".into(),
                size: 100.0,
                color_value: 0.0,
            },
        ];
        let svg = render_treemap(&items, 800.0, 400.0);
        assert!(svg.contains("<svg"), "should produce SVG");
        assert!(svg.contains("</svg>"), "should close SVG");
        assert!(svg.contains("main.rs"), "should contain labels");
        assert!(svg.contains("<rect"), "should contain rectangles");
    }

    #[test]
    fn test_treemap_empty() {
        let svg = render_treemap(&[], 800.0, 400.0);
        assert!(svg.contains("<svg"));
        assert!(svg.contains("</svg>"));
    }

    #[test]
    fn test_treemap_single_item() {
        let items = vec![TreemapItem {
            label: "only.rs".into(),
            size: 100.0,
            color_value: 0.5,
        }];
        let svg = render_treemap(&items, 400.0, 300.0);
        assert!(svg.contains("only.rs"));
    }
}
