/**
 * Intentionally bad Java code for integration testing.
 * Contains code smells, security vulnerabilities, and quality issues
 * that Repotoire detectors should flag.
 */

import java.io.*;
import java.sql.*;
import java.security.*;
import java.util.logging.*;
import javax.crypto.*;
import javax.xml.parsers.*;
import org.w3c.dom.*;

public class Smells {

    private static final Logger logger = Logger.getLogger(Smells.class.getName());

    // ---------------------------------------------------------------
    // Empty catch blocks (empty-catch-block detector)
    // Single-line format required for Java detection
    // ---------------------------------------------------------------
    public void emptyExceptionHandler(String path) {
        try { new FileInputStream(path).read(); } catch (IOException e) {}

        try { Thread.sleep(1000); } catch (InterruptedException e) {}

        try { Class.forName("com.example.Driver"); } catch (ClassNotFoundException e) { }
    }

    // ---------------------------------------------------------------
    // Deep nesting — 6 levels (deep-nesting detector)
    // ---------------------------------------------------------------
    public String deeplyNestedLogic(int a, int b, int c, int d) {
        if (a > 0) {
            if (b > 0) {
                for (int i = 0; i < 10; i++) {
                    if (c > 0) {
                        while (d > 0) {
                            if (a + b + c + d > 42) {
                                return "found";
                            }
                            d--;
                        }
                    }
                }
            }
        }
        return "not found";
    }

    // ---------------------------------------------------------------
    // Magic numbers (magic-numbers detector)
    // Uses integers >= 2 digits NOT in the acceptable set
    // ---------------------------------------------------------------
    public double calculatePrice(double base) {
        int bufferSize = 86400000;
        int retryDelay = 31337;
        int batchLimit = 99999;
        int portNumber = 54321;
        return base * bufferSize / retryDelay + batchLimit - portNumber;
    }

    // ---------------------------------------------------------------
    // SQL injection (SQLInjectionDetector)
    // ---------------------------------------------------------------
    public ResultSet unsafeQuery(Connection conn, String userInput) throws SQLException {
        String query = "SELECT * FROM users WHERE name = '" + userInput + "'";
        Statement stmt = conn.createStatement();
        return stmt.execute(query) ? stmt.getResultSet() : null;
    }

    public void unsafeUpdate(Connection conn, String userInput) throws SQLException {
        Statement stmt = conn.createStatement();
        stmt.execute("DELETE FROM sessions WHERE token = '" + userInput + "'");
    }

    // ---------------------------------------------------------------
    // Command injection (command-injection detector)
    // ---------------------------------------------------------------
    public void unsafeExec(String userInput) throws IOException {
        Runtime.getRuntime().exec("cmd /c " + userInput);
    }

    public void unsafeProcessBuilder(String userInput) throws IOException {
        ProcessBuilder pb = new ProcessBuilder("sh", "-c", userInput);
        pb.start();
    }

    // ---------------------------------------------------------------
    // Insecure crypto (insecure-crypto detector)
    // ---------------------------------------------------------------
    public byte[] encryptPayload(byte[] data) throws Exception {
        Cipher cipher = Cipher.getInstance("DES");
        SecretKey key = KeyGenerator.getInstance("DES").generateKey();
        cipher.init(Cipher.ENCRYPT_MODE, key);
        return cipher.doFinal(data);
    }

    public byte[] hashPayload(byte[] data) throws Exception {
        MessageDigest md = MessageDigest.getInstance("MD5");
        return md.digest(data);
    }

    public byte[] encryptBlock(byte[] data) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/ECB/PKCS5Padding");
        return cipher.doFinal(data);
    }

    // ---------------------------------------------------------------
    // XXE — XML parsing without DTD disabled (xxe detector)
    // ---------------------------------------------------------------
    public Document parseXmlUnsafe(InputStream input) throws Exception {
        DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
        DocumentBuilder builder = factory.newDocumentBuilder();
        return builder.parse(input);
    }

    public void saxParseUnsafe(InputStream input) throws Exception {
        SAXParserFactory factory = SAXParserFactory.newInstance();
        SAXParser parser = factory.newSAXParser();
        parser.parse(input, new org.xml.sax.helpers.DefaultHandler());
    }

    // ---------------------------------------------------------------
    // Log injection (log-injection detector)
    // ---------------------------------------------------------------
    public void logUserInput(String userInput) {
        logger.info("User login attempt: " + userInput);
    }

    public void logRequest(String requestParam) {
        logger.warning("Request received with input: " + requestParam);
    }

    // ---------------------------------------------------------------
    // Insecure deserialization (insecure-deserialize detector)
    // Note: deserialize keyword needed for content flag
    // ---------------------------------------------------------------
    public Object unsafeDeserialize(byte[] data) throws Exception {
        ByteArrayInputStream bais = new ByteArrayInputStream(data);
        ObjectInputStream ois = new ObjectInputStream(bais);
        return ois.readObject();
    }

    public Object deserializeFromFile(String path) throws Exception {
        FileInputStream fis = new FileInputStream(path);
        ObjectInputStream ois = new ObjectInputStream(fis);
        Object obj = ois.readObject();
        ois.close();
        return obj;
    }

    // ---------------------------------------------------------------
    // Additional code-quality smells
    // ---------------------------------------------------------------

    // Boolean trap
    public void configure(boolean verbose, boolean strict, boolean fast) {
        if (verbose) { System.out.println("verbose"); }
        if (strict) { System.out.println("strict"); }
        if (fast) { System.out.println("fast"); }
    }

    // Long parameter list
    public void processOrder(String name, String address, String city,
                             String state, String zip, String country,
                             String phone, String email, double amount) {
        System.out.println(name + address + city + state + zip + country + phone + email + amount);
    }

    // Dead store
    public int deadStoreExample() {
        int x = computeValue();
        x = 42;
        return x;
    }

    private int computeValue() {
        return 100;
    }
}
