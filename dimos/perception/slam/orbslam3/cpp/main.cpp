// Copyright 2026 Dimensional Inc.
// SPDX-License-Identifier: Apache-2.0
//
// ORB-SLAM3 native module for dimos NativeModule framework.
//
// Subscribes to camera images on LCM, feeds them to ORB_SLAM3::TrackMonocular(),
// and publishes camera pose estimates as nav_msgs::Odometry.
//
// Usage:
//   ./orbslam3_native \
//       --color_image '/color_image#sensor_msgs.Image' \
//       --odometry '/odometry#nav_msgs.Odometry' \
//       --settings_path /path/to/RealSense_D435i.yaml \
//       --sensor_mode MONOCULAR \
//       --use_viewer false

#include <lcm/lcm-cpp.hpp>

#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>

#include <opencv2/core/core.hpp>
#include <opencv2/imgproc/imgproc.hpp>

#include "dimos_native_module.hpp"

// dimos LCM message headers
#include "geometry_msgs/Point.hpp"
#include "geometry_msgs/Quaternion.hpp"
#include "nav_msgs/Odometry.hpp"
#include "sensor_msgs/Image.hpp"

// ORB-SLAM3
#include "System.h"

// ---------------------------------------------------------------------------
// Global state
// ---------------------------------------------------------------------------

static std::atomic<bool> g_running{true};
static lcm::LCM* g_lcm = nullptr;
static ORB_SLAM3::System* g_slam = nullptr;

static std::string g_image_topic;
static std::string g_odometry_topic;
static std::string g_frame_id = "map";
static std::string g_child_frame_id = "camera";

static int g_frame_count = 0;

// ---------------------------------------------------------------------------
// Signal handling
// ---------------------------------------------------------------------------

static void signal_handler(int /*sig*/) {
    g_running.store(false);
}

// ---------------------------------------------------------------------------
// Sensor mode parsing
// ---------------------------------------------------------------------------

static ORB_SLAM3::System::eSensor parse_sensor_mode(const std::string& mode) {
    if (mode == "MONOCULAR")     return ORB_SLAM3::System::MONOCULAR;
    if (mode == "STEREO")        return ORB_SLAM3::System::STEREO;
    if (mode == "RGBD")          return ORB_SLAM3::System::RGBD;
    if (mode == "IMU_MONOCULAR") return ORB_SLAM3::System::IMU_MONOCULAR;
    if (mode == "IMU_STEREO")    return ORB_SLAM3::System::IMU_STEREO;
    if (mode == "IMU_RGBD")      return ORB_SLAM3::System::IMU_RGBD;
    fprintf(stderr, "[orbslam3] Unknown sensor mode: %s, defaulting to MONOCULAR\n",
            mode.c_str());
    return ORB_SLAM3::System::MONOCULAR;
}

// ---------------------------------------------------------------------------
// Image decoding
// ---------------------------------------------------------------------------

using dimos::time_from_seconds;
using dimos::make_header;

static cv::Mat decode_image(const sensor_msgs::Image& msg) {
    int cv_type;
    int channels;

    if (msg.encoding == "mono8") {
        cv_type = CV_8UC1;
        channels = 1;
    } else if (msg.encoding == "rgb8") {
        cv_type = CV_8UC3;
        channels = 3;
    } else if (msg.encoding == "bgr8") {
        cv_type = CV_8UC3;
        channels = 3;
    } else if (msg.encoding == "rgba8") {
        cv_type = CV_8UC4;
        channels = 4;
    } else if (msg.encoding == "bgra8") {
        cv_type = CV_8UC4;
        channels = 4;
    } else {
        // Fallback: treat as mono8
        fprintf(stderr, "[orbslam3] Unknown encoding '%s', treating as mono8\n",
                msg.encoding.c_str());
        cv_type = CV_8UC1;
        channels = 1;
    }

    // Wrap raw data in cv::Mat (zero-copy from LCM buffer)
    cv::Mat raw(msg.height, msg.width, cv_type,
                const_cast<uint8_t*>(msg.data.data()), msg.step);

    // Convert to grayscale
    cv::Mat gray;
    if (channels == 1) {
        gray = raw.clone();
    } else if (msg.encoding == "rgb8") {
        cv::cvtColor(raw, gray, cv::COLOR_RGB2GRAY);
    } else if (msg.encoding == "bgr8") {
        cv::cvtColor(raw, gray, cv::COLOR_BGR2GRAY);
    } else if (msg.encoding == "rgba8") {
        cv::cvtColor(raw, gray, cv::COLOR_RGBA2GRAY);
    } else if (msg.encoding == "bgra8") {
        cv::cvtColor(raw, gray, cv::COLOR_BGRA2GRAY);
    } else {
        gray = raw.clone();
    }

    return gray;
}

static double image_timestamp(const sensor_msgs::Image& msg) {
    return static_cast<double>(msg.header.stamp.sec) +
           static_cast<double>(msg.header.stamp.nsec) / 1e9;
}

// ---------------------------------------------------------------------------
// Publish odometry
// ---------------------------------------------------------------------------

static void publish_odometry(const Sophus::SE3f& pose, double timestamp) {
    if (!g_lcm || g_odometry_topic.empty()) return;

    // Extract translation and quaternion
    Eigen::Vector3f t = pose.translation();
    Eigen::Quaternionf q = pose.unit_quaternion();

    nav_msgs::Odometry msg;
    msg.header = make_header(g_frame_id, timestamp);
    msg.child_frame_id = g_child_frame_id;

    // Position
    msg.pose.pose.position.x = static_cast<double>(t.x());
    msg.pose.pose.position.y = static_cast<double>(t.y());
    msg.pose.pose.position.z = static_cast<double>(t.z());

    // Orientation (Eigen quaternion: x,y,z,w)
    msg.pose.pose.orientation.x = static_cast<double>(q.x());
    msg.pose.pose.orientation.y = static_cast<double>(q.y());
    msg.pose.pose.orientation.z = static_cast<double>(q.z());
    msg.pose.pose.orientation.w = static_cast<double>(q.w());

    // Diagonal covariance (ORB-SLAM3 doesn't provide covariance)
    std::memset(msg.pose.covariance, 0, sizeof(msg.pose.covariance));
    for (int i = 0; i < 6; ++i) {
        msg.pose.covariance[i * 6 + i] = 0.01;
    }

    // Zero twist
    msg.twist.twist.linear.x = 0;
    msg.twist.twist.linear.y = 0;
    msg.twist.twist.linear.z = 0;
    msg.twist.twist.angular.x = 0;
    msg.twist.twist.angular.y = 0;
    msg.twist.twist.angular.z = 0;
    std::memset(msg.twist.covariance, 0, sizeof(msg.twist.covariance));

    g_lcm->publish(g_odometry_topic, &msg);
}

// ---------------------------------------------------------------------------
// Image handler
// ---------------------------------------------------------------------------

class ImageHandler {
public:
    void on_image(const lcm::ReceiveBuffer* /*rbuf*/,
                  const std::string& /*channel*/,
                  const sensor_msgs::Image* msg) {
        if (!g_slam || !g_running.load()) return;

        // Decode image to grayscale
        cv::Mat gray = decode_image(*msg);
        if (gray.empty()) return;

        double ts = image_timestamp(*msg);

        // Track
        Sophus::SE3f Tcw = g_slam->TrackMonocular(gray, ts);

        // Only publish when actively tracking (state 2 = eTracking)
        int state = g_slam->GetTrackingState();
        if (state == 2) {
            // Invert: TrackMonocular returns Tcw (world-to-camera),
            // we want Twc (camera pose in world frame)
            Sophus::SE3f Twc = Tcw.inverse();
            publish_odometry(Twc, ts);
        }

        g_frame_count++;
        if (g_frame_count % 100 == 0) {
            const char* state_str = "unknown";
            switch (state) {
                case 0: state_str = "not_initialized"; break;
                case 1: state_str = "initializing"; break;
                case 2: state_str = "tracking"; break;
                case 3: state_str = "lost"; break;
            }
            printf("[orbslam3] frame=%d state=%s\n", g_frame_count, state_str);
        }
    }
};

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

int main(int argc, char** argv) {
    dimos::NativeModule mod(argc, argv);

    // Required: camera settings YAML
    std::string settings_path = mod.arg("settings_path", "");
    if (settings_path.empty()) {
        fprintf(stderr, "[orbslam3] Error: --settings_path is required\n");
        return 1;
    }

    // Vocabulary (compile-time default from nix build, or override via CLI)
#ifdef ORBSLAM3_DEFAULT_VOCAB
    std::string vocab_path = mod.arg("vocab_path", ORBSLAM3_DEFAULT_VOCAB);
#else
    std::string vocab_path = mod.arg("vocab_path", "");
#endif
    if (vocab_path.empty()) {
        fprintf(stderr, "[orbslam3] Error: --vocab_path is required "
                        "(no compiled-in default available)\n");
        return 1;
    }

    // Topics
    g_image_topic = mod.has("color_image") ? mod.topic("color_image") : "";
    g_odometry_topic = mod.has("odometry") ? mod.topic("odometry") : "";

    if (g_image_topic.empty()) {
        fprintf(stderr, "[orbslam3] Error: --color_image topic is required\n");
        return 1;
    }

    // Config
    std::string sensor_str = mod.arg("sensor_mode", "MONOCULAR");
    bool use_viewer = mod.arg("use_viewer", "false") == "true";
    g_frame_id = mod.arg("frame_id", "map");
    g_child_frame_id = mod.arg("child_frame_id", "camera");
    auto sensor_mode = parse_sensor_mode(sensor_str);

    printf("[orbslam3] Starting ORB-SLAM3 native module\n");
    printf("[orbslam3]   vocab:    %s\n", vocab_path.c_str());
    printf("[orbslam3]   settings: %s\n", settings_path.c_str());
    printf("[orbslam3]   sensor:   %s\n", sensor_str.c_str());
    printf("[orbslam3]   viewer:   %s\n", use_viewer ? "true" : "false");
    printf("[orbslam3]   image topic:    %s\n", g_image_topic.c_str());
    printf("[orbslam3]   odometry topic: %s\n",
           g_odometry_topic.empty() ? "(disabled)" : g_odometry_topic.c_str());

    // Signal handlers
    signal(SIGTERM, signal_handler);
    signal(SIGINT, signal_handler);

    // Init LCM
    lcm::LCM lcm;
    if (!lcm.good()) {
        fprintf(stderr, "[orbslam3] Error: LCM init failed\n");
        return 1;
    }
    g_lcm = &lcm;

    // Subscribe to image topic
    ImageHandler handler;
    lcm.subscribe(g_image_topic, &ImageHandler::on_image, &handler);

    // Initialize ORB-SLAM3 (loads vocabulary, starts internal threads)
    printf("[orbslam3] Loading vocabulary...\n");
    ORB_SLAM3::System slam(vocab_path, settings_path, sensor_mode, use_viewer);
    g_slam = &slam;

    printf("[orbslam3] System initialized, processing frames.\n");

    // Main loop: dispatch LCM messages
    while (g_running.load()) {
        lcm.handleTimeout(100);
    }

    // Cleanup
    printf("[orbslam3] Shutting down... (%d frames processed)\n", g_frame_count);
    g_slam = nullptr;
    slam.Shutdown();
    g_lcm = nullptr;

    printf("[orbslam3] Done.\n");
    return 0;
}
