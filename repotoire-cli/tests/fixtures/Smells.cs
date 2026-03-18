// A deliberately smelly C# file for integration testing.
// This file contains intentional code quality issues that detectors should catch.

using System;
using System.Collections.Generic;
using System.Data.SqlClient;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;

namespace Repotoire.Tests.Fixtures
{
    public class SmellsService
    {
        private readonly string _connectionString;
        private readonly HttpClient _client;
        private Dictionary<string, object> _cache;
        private int _retryCount;

        public SmellsService(string connectionString)
        {
            _connectionString = connectionString;
            _client = new HttpClient();
            _cache = new Dictionary<string, object>();
            _retryCount = 3;
        }

        // ================================================================
        // Long method (>100 lines) — should trigger LongMethodsDetector
        // ================================================================
        public string ProcessOrder(int orderId, string customerId, double amount)
        {
            var result = new StringBuilder();
            var timestamp = DateTime.UtcNow;

            // Step 1: validate inputs
            if (orderId <= 0)
            {
                result.AppendLine("Invalid order id");
                return result.ToString();
            }
            if (string.IsNullOrEmpty(customerId))
            {
                result.AppendLine("Invalid customer id");
                return result.ToString();
            }
            if (amount <= 0)
            {
                result.AppendLine("Invalid amount");
                return result.ToString();
            }

            // Step 2: check inventory
            var inventory = GetInventoryCount(orderId);
            if (inventory < 1)
            {
                result.AppendLine("Out of stock");
                return result.ToString();
            }

            // Step 3: apply discounts
            double discount = 0.0;
            if (amount > 500)
            {
                discount = amount * 0.15;
            }
            else if (amount > 200)
            {
                discount = amount * 0.10;
            }
            else if (amount > 100)
            {
                discount = amount * 0.05;
            }
            double finalAmount = amount - discount;

            // Step 4: tax calculation
            double taxRate = 0.08;
            double tax = finalAmount * taxRate;
            double total = finalAmount + tax;

            // Step 5: payment processing
            result.AppendLine($"Order {orderId} for customer {customerId}");
            result.AppendLine($"Subtotal: {amount:C}");
            result.AppendLine($"Discount: {discount:C}");
            result.AppendLine($"Tax: {tax:C}");
            result.AppendLine($"Total: {total:C}");

            // Step 6: update records
            var status = "pending";
            if (total > 1000)
            {
                status = "review";
            }

            // Step 7: build confirmation
            result.AppendLine($"Status: {status}");
            result.AppendLine($"Timestamp: {timestamp}");

            // Step 8: loyalty points
            int points = (int)(total / 10);
            if (points > 100)
            {
                points = 100;
            }
            result.AppendLine($"Points earned: {points}");

            // Step 9: shipping estimate
            int shippingDays = 5;
            if (total > 500)
            {
                shippingDays = 2;
            }
            else if (total > 200)
            {
                shippingDays = 3;
            }
            result.AppendLine($"Estimated delivery: {shippingDays} days");

            // Step 10: notification prep
            var notificationMessage = $"Dear customer {customerId}, your order {orderId} " +
                $"totaling {total:C} has been placed. " +
                $"Estimated delivery in {shippingDays} days.";
            result.AppendLine($"Notification: {notificationMessage}");

            // Step 11: audit logging
            var auditEntry = $"[{timestamp}] Order={orderId} Customer={customerId} " +
                $"Amount={amount} Discount={discount} Tax={tax} Total={total} " +
                $"Status={status} Points={points} Shipping={shippingDays}";
            result.AppendLine($"Audit: {auditEntry}");

            // Step 12: final validation
            if (total <= 0)
            {
                result.AppendLine("ERROR: Negative total after calculation");
                return result.ToString();
            }

            // Step 13: return code generation
            var confirmationCode = $"ORD-{orderId}-{customerId.GetHashCode():X8}";
            result.AppendLine($"Confirmation: {confirmationCode}");

            return result.ToString();
        }

        // ================================================================
        // Empty catch blocks — should trigger EmptyCatchDetector
        // ================================================================
        public string FetchData(string url)
        {
            try
            {
                var response = _client.GetStringAsync(url).Result;
                return response;
            }
            catch (HttpRequestException)
            {
            }
            catch (Exception)
            {
            }
            return string.Empty;
        }

        public int ParseValue(string input)
        {
            try
            {
                return int.Parse(input);
            }
            catch (FormatException)
            {
                // silently swallowed
            }
            catch (OverflowException) { }
            return 0;
        }

        // ================================================================
        // Deep nesting (5+ levels) — should trigger DeepNestingDetector
        // ================================================================
        public string DeepNestingExample(List<Dictionary<string, List<int>>> data)
        {
            if (data != null)
            {
                foreach (var dict in data)
                {
                    if (dict != null)
                    {
                        foreach (var kvp in dict)
                        {
                            if (kvp.Value != null)
                            {
                                foreach (var num in kvp.Value)
                                {
                                    if (num > 0)
                                    {
                                        if (num % 2 == 0)
                                        {
                                            return $"Found even positive: {num}";
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            return "nothing found";
        }

        // ================================================================
        // Magic numbers — should trigger MagicNumbersDetector
        // ================================================================
        public string ClassifyUser(int score, int age, double balance)
        {
            if (score > 850)
            {
                return "platinum";
            }
            if (score > 700 && age >= 25)
            {
                return "gold";
            }
            if (balance > 10000.0 && score > 600)
            {
                return "silver";
            }
            if (age < 18)
            {
                return "junior";
            }
            if (score < 300)
            {
                return "restricted";
            }
            return "standard";
        }

        public double CalculateShipping(double weight, int zone)
        {
            double base_cost = 4.99;
            if (weight > 50.0)
            {
                base_cost += 15.75;
            }
            else if (weight > 20.0)
            {
                base_cost += 8.50;
            }
            if (zone > 3)
            {
                base_cost *= 1.35;
            }
            return base_cost;
        }

        // ================================================================
        // TODO comments — should trigger TodoScanner
        // ================================================================

        // TODO: refactor this entire class into smaller services
        // TODO: add proper logging framework instead of Console.WriteLine
        // TODO(zhammad): fix the race condition in the cache lookup
        // FIXME: memory leak when processing large batches
        // HACK: temporary workaround for the API rate limit

        private int GetInventoryCount(int productId)
        {
            // TODO: replace stub with real inventory lookup
            return 42;
        }

        // ================================================================
        // Commented-out code blocks — should trigger CommentedCodeDetector
        // ================================================================

        // public void OldProcessPayment(string cardNumber, decimal amount)
        // {
        //     var client = new HttpClient();
        //     var payload = new { card = cardNumber, amount = amount };
        //     var json = Newtonsoft.Json.JsonConvert.SerializeObject(payload);
        //     var content = new StringContent(json, Encoding.UTF8, "application/json");
        //     var response = client.PostAsync("https://api.payments.com/charge", content).Result;
        //     if (!response.IsSuccessStatusCode)
        //     {
        //         throw new Exception("Payment failed: " + response.StatusCode);
        //     }
        //     Console.WriteLine("Payment processed: " + amount);
        // }

        // public List<string> GetLegacyReports(DateTime startDate, DateTime endDate)
        // {
        //     var reports = new List<string>();
        //     using (var conn = new SqlConnection(_connectionString))
        //     {
        //         conn.Open();
        //         var cmd = new SqlCommand(
        //             $"SELECT * FROM reports WHERE date BETWEEN '{startDate}' AND '{endDate}'",
        //             conn);
        //         var reader = cmd.ExecuteReader();
        //         while (reader.Read())
        //         {
        //             reports.Add(reader.GetString(0));
        //         }
        //     }
        //     return reports;
        // }
    }
}
