import random

class TRPGEngine:
    @staticmethod
    def roll_d20(bonus=0):
        """擲 1d20 + 加成"""
        return random.randint(1, 20) + bonus

    @staticmethod
    def get_scaling_factor(level):
        """
        計算經驗增幅次方項。
        """
        return 1 + (level * 0.01)
    
    @staticmethod
    def get_shop_price(item, stage):
        """計算商店動態價格"""
        base_price = item.get("gold", 100)
        # 僅消耗品 (heal, boost) 會隨著關卡大幅通膨
        if item["type"] in ["heal", "boost", "boost-all"]:
            multiplier = 1 + (stage // 5)
            return int(base_price * multiplier)
        # 裝備類維持原價或小幅增長
        return base_price
    
    @staticmethod
    def get_total_stats(player, items_db):
        """計算包含裝備加成的最終屬性"""
        final_stats = player["attributes"].copy()
        equips = player.get("equips", {})
        
        for slot, item_id in equips.items():
            if item_id:
                item_data = items_db.get(item_id)
                if item_data and "stats" in item_data:
                    for stat, val in item_data["stats"].items():
                        final_stats[stat] = final_stats.get(stat, 10) + val
        return final_stats

    @staticmethod
    def calculate_combat(player, monster, job_config, final_stats):
        """
        傳入 job_config 參數字典，不再依賴 job 名稱判斷
        job_config 應包含: crit_chance, crit_mult, flat_bonus_atk, hp_cost_per_atk
        """
        if monster is None:
            return 0, 0

        damage_type = job_config.get("damage_type", "p")
        if damage_type == "p":
            p_atk = final_stats["STR"]
        elif damage_type == "m":
            p_atk = final_stats["INT"]
        else:
            p_atk = final_stats["STR"]+final_stats["INT"]
        
        # 1. 處理扣血換傷 (血色祭司類)
        hp_cost = job_config.get("hp_cost_per_atk", 0)
        atk_bonus = job_config.get("flat_bonus_atk", 0)
        
        if hp_cost > 0 and player['health'] > hp_cost:
            player['health'] -= hp_cost
        else:
            atk_bonus = 0 # 若血量不足不扣血也不給加成
            
        # 2. 處理暴擊 (破壁者類)
        crit_mult = 1.0
        # 爆擊機率 = 
        # 只有當玩家攻擊力大於怪物防禦時觸發 (保留原本邏輯)
        if p_atk > monster.get("def", 0): # 攻擊力>防禦才會出發爆擊
            if random.random() < (job_config.get("crit_chance", 0) + (final_stats["LUK"]/250)):
                crit_mult = 1 + job_config.get("crit_mult", 0.0)+ (final_stats["LUK"]/500)

        # 3. 傷害計算公式
        # 修改: 將隨機波動與加成納入計算
        base_dmg = max(1, (p_atk // 1.5) * crit_mult - monster['def'])
        final_dmg = int(base_dmg + (random.randint(1, 7)-4) + (int(atk_bonus*final_stats['INT']/10)))
        player_dmg = max(1,  final_dmg)
        monster['hp'] -= player_dmg
        
        # 4. 怪物反擊
        monster_dmg = 0
        if monster['hp'] > 0:
            # 敏捷減傷公式優化
            monster_dmg = max(1, monster['atk'] - (final_stats['DEX'] // job_config.get("dodge_div", 4)))
            player['health'] -= monster_dmg
            
        return player_dmg, monster_dmg

    @staticmethod
    def get_required_exp(level):
        return int(level**TRPGEngine.get_scaling_factor(level)) * 50 

    @staticmethod
    def check_level_up(player):
        required = TRPGEngine.get_required_exp(player.get("level", 1))
        return player.get("exp", 0) >= required