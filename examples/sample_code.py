"""Sample Python code for testing Falkor analysis."""


class DataProcessor:
    """Processes data from various sources."""

    def __init__(self, config: dict):
        """Initialize processor with configuration.

        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.cache = {}

    def process(self, data: list) -> list:
        """Process input data.

        Args:
            data: Input data list

        Returns:
            Processed data
        """
        results = []
        for item in data:
            if self._is_valid(item):
                processed = self._transform(item)
                results.append(processed)
        return results

    def _is_valid(self, item: dict) -> bool:
        """Validate data item."""
        return item is not None and "value" in item

    def _transform(self, item: dict) -> dict:
        """Transform data item."""
        return {"processed": item["value"] * 2}


def calculate_metrics(processor: DataProcessor, data: list) -> dict:
    """Calculate metrics from processed data.

    Args:
        processor: Data processor instance
        data: Input data

    Returns:
        Metrics dictionary
    """
    processed = processor.process(data)
    return {"count": len(processed), "total": sum(p["processed"] for p in processed)}


# Example usage
if __name__ == "__main__":
    config = {"mode": "test"}
    processor = DataProcessor(config)
    sample_data = [{"value": 1}, {"value": 2}, {"value": 3}]
    metrics = calculate_metrics(processor, sample_data)
    print(f"Metrics: {metrics}")
