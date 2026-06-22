// dump_map — 加载已保存的地图数据库(.msg)，把 landmarks + 关键帧轨迹导出为 map.json，
// 供网页"观察模式"只读显示。不开相机、不跟踪。秒级。
//   dump_map -v vocab -c config -i map.msg -o /path/map.json [--label "观察存图"]
#include "stella_vslam/system.h"
#include "stella_vslam/config.h"
#include "stella_vslam/type.h"
#include "stella_vslam/publish/map_publisher.h"
#include "stella_vslam/data/landmark.h"
#include "stella_vslam/data/keyframe.h"

#include <iostream>
#include <fstream>
#include <sstream>
#include <vector>
#include <set>
#include <memory>
#include <cstdio>
#include <string>

#include <spdlog/spdlog.h>
#include <popl.hpp>

static void write_map_json(const std::shared_ptr<stella_vslam::publish::map_publisher>& map_pub,
                           const std::string& path, const std::string& state) {
    std::vector<std::shared_ptr<stella_vslam::data::landmark>> lms;
    std::set<std::shared_ptr<stella_vslam::data::landmark>> local;
    map_pub->get_landmarks(lms, local);
    std::vector<std::shared_ptr<stella_vslam::data::keyframe>> kfs;
    map_pub->get_keyframes(kfs);

    std::ostringstream os;
    os.setf(std::ios::fixed);
    os.precision(4);
    // 观察模式无实时相机，cam 置原点（网页据 state 决定不画当前相机）
    os << "{\"state\":\"" << state << "\",\"n_lm\":" << lms.size()
       << ",\"n_kf\":" << kfs.size() << ",\"cam\":[0,0,0],\"traj\":[";
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
    std::rename(tmp.c_str(), path.c_str());
}

int main(int argc, char* argv[]) {
    popl::OptionParser op("dump_map: 加载 .msg 地图导出 map.json (观察用，不开相机)");
    auto help = op.add<popl::Switch>("h", "help", "produce help message");
    auto vocab_file = op.add<popl::Value<std::string>>("v", "vocab", "vocabulary file path");
    auto config_file = op.add<popl::Value<std::string>>("c", "config", "config file path");
    auto map_in = op.add<popl::Value<std::string>>("i", "map-db-in", "map database to load");
    auto out_path = op.add<popl::Value<std::string>>("o", "out", "output map.json path", "map.json");
    auto label = op.add<popl::Value<std::string>>("", "label", "state label for the viewer", "观察存图");
    try {
        op.parse(argc, argv);
    }
    catch (const std::exception& e) {
        std::cerr << e.what() << std::endl << op << std::endl;
        return EXIT_FAILURE;
    }
    if (help->is_set() || !vocab_file->is_set() || !config_file->is_set() || !map_in->is_set()) {
        std::cerr << op << std::endl;
        return EXIT_FAILURE;
    }

    std::shared_ptr<stella_vslam::config> cfg;
    try {
        cfg = std::make_shared<stella_vslam::config>(config_file->value());
    }
    catch (const std::exception& e) {
        std::cerr << e.what() << std::endl;
        return EXIT_FAILURE;
    }

    auto slam = std::make_shared<stella_vslam::system>(cfg, vocab_file->value());
    if (!slam->load_map_database(map_in->value())) {
        spdlog::error("load_map_database failed: {}", map_in->value());
        return EXIT_FAILURE;
    }
    slam->startup(false); // 已有地图，不做初始化
    auto map_pub = slam->get_map_publisher();
    write_map_json(map_pub, out_path->value(), label->value());
    slam->shutdown();
    spdlog::info("dumped {} -> {}", map_in->value(), out_path->value());
    return EXIT_SUCCESS;
}
