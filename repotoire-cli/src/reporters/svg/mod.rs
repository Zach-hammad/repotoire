//! Pure SVG generation for report visualizations.
//! No JavaScript, no external dependencies.

pub mod treemap;
pub mod architecture;
pub mod bar_chart;

/// Generate an SVG color on a green-to-red gradient based on a 0.0-1.0 value.
/// 0.0 = green (#10b981), 0.5 = yellow (#eab308), 1.0 = red (#ef4444)
pub fn health_color(value: f64) -> String {
    let v = value.clamp(0.0, 1.0);
    if v < 0.5 {
        let t = v * 2.0;
        let r = (16.0 + (234.0 - 16.0) * t) as u8;
        let g = (185.0 + (179.0 - 185.0) * t) as u8;
        let b = (129.0 + (8.0 - 129.0) * t) as u8;
        format!("#{:02x}{:02x}{:02x}", r, g, b)
    } else {
        let t = (v - 0.5) * 2.0;
        let r = (234.0 + (239.0 - 234.0) * t) as u8;
        let g = (179.0 + (68.0 - 179.0) * t) as u8;
        let b = (8.0 + (68.0 - 8.0) * t) as u8;
        format!("#{:02x}{:02x}{:02x}", r, g, b)
    }
}

/// Escape text for use inside SVG/XML.
pub fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_health_color_green() {
        assert_eq!(health_color(0.0), "#10b981");
    }

    #[test]
    fn test_health_color_red() {
        assert_eq!(health_color(1.0), "#ef4444");
    }

    #[test]
    fn test_xml_escape() {
        assert_eq!(xml_escape("<hello>"), "&lt;hello&gt;");
    }
}
