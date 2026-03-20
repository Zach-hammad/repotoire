//! Horizontal bar chart SVG generator.

use super::xml_escape;

pub struct BarItem {
    pub label: String,
    pub value: f64, // 0.0 - 1.0
    pub color: String, // hex color
}

/// Render a horizontal bar chart as inline SVG.
pub fn render_bar_chart(items: &[BarItem], title: &str, width: f64, height: f64) -> String {
    let h = if height == 0.0 {
        (items.len() * 32 + 50) as f64
    } else {
        height
    };
    let max_bar_width = width - 180.0;

    let mut svg = format!(
        r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" style="font-family: sans-serif;">
  <text x="10" y="24" font-size="14" font-weight="bold">{title}</text>
"#,
        w = width,
        h = h,
        title = xml_escape(title),
    );

    for (i, item) in items.iter().enumerate() {
        let y = 50 + i * 32;
        let bar_w = (item.value.clamp(0.0, 1.0) * max_bar_width).round();
        let bar_end = 130.0 + bar_w;
        let pct = (item.value * 100.0).round() as i64;
        let label_fill = "#475569";

        svg.push_str(&format!(
            "  <text x=\"10\" y=\"{y}\" font-size=\"11\" fill=\"{label_fill}\">{label}</text>\n  <rect x=\"130\" y=\"{rect_y}\" width=\"{bar_w}\" height=\"20\" rx=\"3\" fill=\"{color}\"/>\n  <text x=\"{text_x}\" y=\"{y}\" font-size=\"11\" fill=\"{label_fill}\">{pct}%</text>\n",
            y = y,
            label = xml_escape(&item.label),
            rect_y = y - 14,
            bar_w = bar_w,
            color = xml_escape(&item.color),
            text_x = bar_end + 8.0,
            pct = pct,
            label_fill = label_fill,
        ));
    }

    svg.push_str("</svg>");
    svg
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_bar_chart_renders_svg() {
        let items = vec![
            BarItem { label: "src/auth".into(), value: 0.8, color: "#ef4444".into() },
            BarItem { label: "src/api".into(), value: 0.4, color: "#f97316".into() },
        ];
        let svg = render_bar_chart(&items, "Bus Factor Risk", 600.0, 0.0);
        assert!(svg.contains("<svg"));
        assert!(svg.contains("auth"));
        assert!(svg.contains("<rect"));
        assert!(svg.contains("Bus Factor"));
    }

    #[test]
    fn test_empty_chart() {
        let svg = render_bar_chart(&[], "Empty", 400.0, 0.0);
        assert!(svg.contains("<svg"));
    }
}
