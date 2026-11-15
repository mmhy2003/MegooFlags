<?php

declare(strict_types=1);

/*
 * Router for the fake server (php -S 127.0.0.1:<port> router.php). The
 * directory in MEGOOFLAGS_FAKE_DIR holds scenario.json, written by
 * FakeServer:
 *
 *   {"<METHOD> <path>": [response, ...]}
 *   response = {"status": int, "headers": {name: value}, "body": string,
 *               "delay": seconds before the headers,
 *               "chunks": [[seconds before, text], ...] sent after body}
 *
 * The responses of a route are served in order and the last one repeats.
 * An unknown route is a 404. Every request is appended to requests.jsonl
 * as {"method", "path", "query", "headers" (lowercase names), "body"}.
 */

$dir = (string) getenv('MEGOOFLAGS_FAKE_DIR');
$method = $_SERVER['REQUEST_METHOD'];
$uri = (string) $_SERVER['REQUEST_URI'];
$path = (string) parse_url($uri, PHP_URL_PATH);
$query = (string) parse_url($uri, PHP_URL_QUERY);

$headers = [];
foreach (getallheaders() as $name => $value) {
    $headers[strtolower((string) $name)] = (string) $value;
}
$body = (string) file_get_contents('php://input');

$lock = fopen($dir . '/router.lock', 'c');
flock($lock, LOCK_EX);

$route = $method . ' ' . $path;
$served = [];
$servedFile = $dir . '/served.json';
if (is_file($servedFile)) {
    $served = json_decode((string) file_get_contents($servedFile), true) ?: [];
}
$scenario = json_decode((string) file_get_contents($dir . '/scenario.json'), true) ?: [];
$responses = $scenario[$route] ?? [['status' => 404, 'body' => 'not found']];
$index = $served[$route] ?? 0;
$response = $responses[min($index, count($responses) - 1)];
$served[$route] = $index + 1;
file_put_contents($servedFile, json_encode($served));

$record = ['method' => $method, 'path' => $path, 'query' => $query, 'headers' => $headers, 'body' => $body];
file_put_contents($dir . '/requests.jsonl', json_encode($record, JSON_UNESCAPED_SLASHES) . "\n", FILE_APPEND);

flock($lock, LOCK_UN);
fclose($lock);

if (($response['delay'] ?? 0) > 0) {
    usleep((int) ($response['delay'] * 1_000_000));
}

http_response_code($response['status'] ?? 200);
foreach ($response['headers'] ?? [] as $name => $value) {
    header($name . ': ' . $value);
}
while (ob_get_level() > 0) {
    ob_end_flush();
}
echo $response['body'] ?? '';
flush();
foreach ($response['chunks'] ?? [] as [$wait, $text]) {
    if ($wait > 0) {
        usleep((int) ($wait * 1_000_000));
    }
    echo $text;
    flush();
}

return true;
