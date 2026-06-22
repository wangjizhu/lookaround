// run_camera_web — monocular stella_vslam on a live camera, exporting the live
// map (landmarks + keyframe trajectory + current camera) as JSON for a headless
// web viewer. No Pangolin/socket/Node/protobuf needed (those are painful on this
// network). Writes <web-dir>/map.json every N frames; `touch <web-dir>/STOP`
// terminates gracefully and saves the map db.
//
// Build: add to stella_vslam_examples (see CMakeLists). Forces V4L2 + MJPG 1280x720.
#include "stella_vslam/system.h"
#include "stella_vslam/config.h"
#include "stella_vslam/type.h"
#include "stella_vslam/publish/map_publisher.h"
#include "stella_vslam/publish/frame_publisher.h"
#include "stella_vslam/data/landmark.h"
#include "stella_vslam/data/keyframe.h"

#include <iostream>
#include <fstream>
#include <sstream>
#include <vector>
#include <set>
#include <memory>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <string>

#include <opencv2/core.hpp>
#include <opencv2/videoio.hpp>
#include <spdlog/spdlog.h>
#include <popl.hpp>

static void write_map_json(const std::shared_ptr<stella_vslam::publish::map_publisher>& map_pub,
                           const std::shared_ptr<stella_vslam::publish::frame_publisher>& frame_pub,
                           const std::string& path) {
    std::vector<std::shared_ptr<stella_vslam::data::landmark>> lms;
    std::set<std::shared_ptr<stella_vslam::data::landmark>> local;
    map_pub->get_landmarks(lms, local);
    std::vector<std::shared_ptr<stella_vslam::data::keyframe>> kfs;
    map_pub->get_keyframes(kfs);
    const stella_vslam::Mat44_t cw = map_pub->get_current_cam_pose();
    const Eigen::Matrix3d R = cw.block<3, 3>(0, 0);
    const Eigen::Vector3d t = cw.block<3, 1>(0, 3);
    const Eigen::Vector3d cc = -R.transpose() * t; // current camera center in world
    const std::string state = frame_pub->get_tracking_state();

    std::ostringstream os;
    os.setf(std::ios::fixed);
    os.precision(4);
    os << "{\"state\":\"" << state << "\",\"n_lm\":" << lms.size()
       << ",\"n_kf\":" << kfs.size()
       << ",\"cam\":[" << cc(0) << "," << cc(1) << "," << cc(2) << "],\"traj\":[";
    bool first = true;
    for (const auto& kf : kfs) {
        if (!kf || kf->will_be_erased()) continue;
        const stella_vslam::Vec3_t c = kf->get_trans_wc();
        if (!first) os << ",";
        first = false;
        os << "[" << c(0) << "," << c(1) << "," << c(2) << "]";
    }
    os << "],\"pts\":[";
    first = true;
    for (const auto& lm : lms) {
        if (!lm || lm->will_be_erased()) continue;
        const stella_vslam::Vec3_t p = lm->get_pos_in_world();
        if (!first) os << ",";
        first = false;
        os << "[" << p(0) << "," << p(1) << "," << p(2) << "]";
    }
    os << "]}";

    const std::string tmp = path + ".tmp";
    std::ofstream f(tmp);
    f << os.str();
    f.close();
    std::rename(tmp.c_str(), path.c_str()); // atomic swap so the viewer never reads a half file
}

int main(int argc, char* argv[]) {
    popl::OptionParser op("run_camera_web: monocular SLAM + JSON map export for a web viewer");
    auto help = op.add<popl::Switch>("h", "help", "produce help message");
    auto vocab_file = op.add<popl::Value<std::string>>("v", "vocab", "vocabulary file path");
    auto config_file = op.add<popl::Value<std::string>>("c", "config", "config file path");
    auto cam_num = op.add<popl::Value<unsigned int>>("n", "number", "camera number", 0);
    auto map_out = op.add<popl::Value<std::string>>("o", "map-db-out", "store map db here on exit", "");
    auto web_dir = op.add<popl::Value<std::string>>("", "web-dir", "dir for map.json / STOP", "");
    auto dump_every = op.add<popl::Value<unsigned int>>("", "dump-every", "dump map every N frames", 10);
    try {
        op.parse(argc, argv);
    }
    catch (const std::exception& e) {
        std::cerr << e.what() << std::endl << op << std::endl;
        return EXIT_FAILURE;
    }
    if (help->is_set() || !vocab_file->is_set() || !config_file->is_set()) {
        std::cerr << op << std::endl;
        return EXIT_FAILURE;
    }

    std::string web = web_dir->value();
    if (web.empty()) {
        const char* h = std::getenv("HOME");
        web = std::string(h ? h : ".") + "/slam/web";
    }
    const std::string json_path = web + "/map.json";
    const std::string stop_path = web + "/STOP";
    const std::string reset_path = web + "/RESET";
    std::remove(stop_path.c_str());
    std::remove(reset_path.c_str());

    std::shared_ptr<stella_vslam::config> cfg;
    try {
        cfg = std::make_shared<stella_vslam::config>(config_file->value());
    }
    catch (const std::exception& e) {
        std::cerr << e.what() << std::endl;
        return EXIT_FAILURE;
    }

    auto slam = std::make_shared<stella_vslam::system>(cfg, vocab_file->value());
    slam->startup();
    auto map_pub = slam->get_map_publisher();
    auto frame_pub = slam->get_frame_publisher();

    cv::VideoCapture video(cam_num->value(), cv::CAP_V4L2);
    video.set(cv::CAP_PROP_FOURCC, cv::VideoWriter::fourcc('M', 'J', 'P', 'G'));
    video.set(cv::CAP_PROP_FRAME_WIDTH, 1280);
    video.set(cv::CAP_PROP_FRAME_HEIGHT, 720);
    spdlog::info("camera opened {}x{}", (int)video.get(cv::CAP_PROP_FRAME_WIDTH), (int)video.get(cv::CAP_PROP_FRAME_HEIGHT));
    if (!video.isOpened()) {
        spdlog::critical("cannot open camera {}", cam_num->value());
        slam->shutdown();
        return EXIT_FAILURE;
    }
    spdlog::info("web SLAM running. map.json -> {} ; `touch {}` to stop", json_path, stop_path);

    cv::Mat frame;
    const cv::Mat mask;
    unsigned int n = 0;
    const unsigned int de = (dump_every->value() == 0) ? 10 : dump_every->value();
    while (true) {
        if (!video.read(frame)) break;
        if (frame.empty()) continue;
        const double ts = std::chrono::duration_cast<std::chrono::duration<double>>(
                              std::chrono::system_clock::now().time_since_epoch())
                              .count();
        slam->feed_monocular_frame(frame, ts, mask);
        if (++n % de == 0) {
            write_map_json(map_pub, frame_pub, json_path);
            std::ifstream rf(reset_path);
            if (rf.good()) {
                rf.close();
                std::remove(reset_path.c_str());
                slam->request_reset(); // 清空地图，回到初始化状态
                spdlog::info("RESET requested: clearing map");
            }
            std::ifstream sf(stop_path);
            if (sf.good()) {
                spdlog::info("STOP file found; terminating");
                break;
            }
        }
        if (slam->terminate_is_requested()) break;
    }

    write_map_json(map_pub, frame_pub, json_path); // final snapshot before shutdown
    slam->shutdown();
    if (!map_out->value().empty()) {
        if (!slam->save_map_database(map_out->value())) {
            spdlog::error("save_map_database failed");
            return EXIT_FAILURE;
        }
        spdlog::info("map saved to {}", map_out->value());
    }
    spdlog::info("done");
    return EXIT_SUCCESS;
}
