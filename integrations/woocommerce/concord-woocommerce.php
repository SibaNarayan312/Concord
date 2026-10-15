<?php
/**
 * Plugin Name: Concord Inventory Reservation for WooCommerce
 * Plugin URI: https://github.com/SibaNarayan312/Concord
 * Description: Integrates WooCommerce checkout and order lifecycles with the Concord High-Contention Atomic Inventory Reservation Service.
 * Version: 1.0.0
 * Author: Concord System Architecture Team
 * Author URI: https://github.com/SibaNarayan312/Concord
 * License: MIT
 */

if (!defined('ABSPATH')) {
    exit; // Exit if accessed directly
}

class Concord_WooCommerce_Integration {
    const OPTION_ENDPOINT = 'concord_endpoint_url';
    const OPTION_WEBHOOK_SECRET = 'concord_webhook_secret';
    const OPTION_HOLD_TTL = 'concord_hold_ttl';

    public function __construct() {
        // Register Admin Settings
        add_action('admin_menu', array($this, 'add_settings_page'));
        add_action('admin_init', array($this, 'register_settings'));

        // Checkout & Order Hooks
        // Hook 1: Create atomic hold immediately when order is processed at checkout
        add_action('woocommerce_checkout_order_processed', array($this, 'on_checkout_order_processed'), 10, 3);

        // Hook 2: Confirm hold when payment is complete
        add_action('woocommerce_payment_complete', array($this, 'on_payment_complete'), 10, 1);

        // Hook 3: Order status transitions (processing, completed, cancelled, failed)
        add_action('woocommerce_order_status_changed', array($this, 'on_order_status_changed'), 10, 4);
    }

    public function get_endpoint() {
        return rtrim(get_option(self::OPTION_ENDPOINT, 'http://localhost:8000'), '/');
    }

    public function get_webhook_secret() {
        return get_option(self::OPTION_WEBHOOK_SECRET, '');
    }

    /**
     * Dispatches order payload to Concord webhook endpoint with HMAC-SHA256 signature
     */
    private function dispatch_to_concord($topic, $order) {
        $endpoint = $this->get_endpoint() . '/woocommerce/webhooks';
        $order_data = $order->get_data();

        // Include line items formatted cleanly
        $line_items = array();
        foreach ($order->get_items() as $item_id => $item) {
            $line_items[] = array(
                'id' => $item_id,
                'name' => $item->get_name(),
                'product_id' => $item->get_product_id(),
                'variation_id' => $item->get_variation_id(),
                'quantity' => $item->get_quantity(),
                'total' => $item->get_total(),
            );
        }
        $order_data['line_items'] = $line_items;

        $body = wp_json_encode($order_data);
        $secret = $this->get_webhook_secret();
        $signature = '';

        if (!empty($secret)) {
            $signature = base64_encode(hash_hmac('sha256', $body, $secret, true));
        }

        $args = array(
            'body' => $body,
            'headers' => array(
                'Content-Type' => 'application/json',
                'x-wc-webhook-topic' => $topic,
                'x-wc-webhook-delivery-id' => wp_generate_uuid4(),
                'x-wc-webhook-signature' => $signature,
            ),
            'timeout' => 5,
        );

        $response = wp_remote_post($endpoint, $args);
        if (is_wp_error($response)) {
            error_log('Concord dispatch error: ' . $response->get_error_message());
        }
    }

    public function on_checkout_order_processed($order_id, $posted_data, $order) {
        $this->dispatch_to_concord('order.created', $order);
    }

    public function on_payment_complete($order_id) {
        $order = wc_get_order($order_id);
        if ($order) {
            $this->dispatch_to_concord('order.updated', $order);
        }
    }

    public function on_order_status_changed($order_id, $from_status, $to_status, $order) {
        $this->dispatch_to_concord('order.updated', $order);
    }

    // --- Admin Settings UI ---
    public function add_settings_page() {
        add_submenu_page(
            'woocommerce',
            'Concord Integration',
            'Concord Reservation',
            'manage_woocommerce',
            'concord-settings',
            array($this, 'render_settings_page')
        );
    }

    public function register_settings() {
        register_setting('concord_options_group', self::OPTION_ENDPOINT);
        register_setting('concord_options_group', self::OPTION_WEBHOOK_SECRET);
        register_setting('concord_options_group', self::OPTION_HOLD_TTL);
    }

    public function render_settings_page() {
        ?>
        <div class="wrap">
            <h1>Concord High-Contention Inventory Reservation Settings</h1>
            <p>Connect your WooCommerce store to the Concord FastAPI Reservation service.</p>
            <form method="post" action="options.php">
                <?php
                settings_fields('concord_options_group');
                do_settings_sections('concord_options_group');
                ?>
                <table class="form-table">
                    <tr valign="top">
                        <th scope="row">Concord API Endpoint URL</th>
                        <td>
                            <input type="url" name="<?php echo esc_attr(self::OPTION_ENDPOINT); ?>"
                                   value="<?php echo esc_attr(get_option(self::OPTION_ENDPOINT, 'http://localhost:8000')); ?>" class="regular-text" required />
                            <p class="description">e.g. <code>https://concord-api.example.com</code></p>
                        </td>
                    </tr>
                    <tr valign="top">
                        <th scope="row">Webhook Shared Secret</th>
                        <td>
                            <input type="password" name="<?php echo esc_attr(self::OPTION_WEBHOOK_SECRET); ?>"
                                   value="<?php echo esc_attr(get_option(self::OPTION_WEBHOOK_SECRET, '')); ?>" class="regular-text" />
                            <p class="description">Must match <code>WC_WEBHOOK_SECRET</code> in Concord's .env file for HMAC-SHA256 signature verification.</p>
                        </td>
                    </tr>
                </table>
                <?php submit_button('Save Concord Settings'); ?>
            </form>
        </div>
        <?php
    }
}

new Concord_WooCommerce_Integration();
